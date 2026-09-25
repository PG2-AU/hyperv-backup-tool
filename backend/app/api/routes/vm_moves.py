"""VM verschieben ueber die App (Nutzer-Vorgabe 2026-09-25). Stufe 1:
Host-Move per Live-Migration innerhalb des Failover-Clusters, als Aktion in
Inventory > VMs. Stufe 2 (Storage-Move) folgt separat.

Sperren (in beide Richtungen, siehe [[feedback_live_vm_testing]] fuer den
Vorfall, bei dem eine Ueberschneidung mit einem Backup einen Checkpoint
beschaedigt hat):
- Kein Move, solange ein Backup-Lauf die VM betrifft (BackupRun.targets
  enthaelt bei VM- UND CSV-Scope die VM-Namen) oder die VM laut letzter
  Discovery einen von der App erstellten Checkpoint (hvnb_*) hat.
- Umgekehrt ueberspringt _execute_job_run (jobs.py) den Checkpoint einer
  VM, fuer die gerade ein Move laeuft (Backup laeuft fuer diese VM crash-
  konsistent weiter).
- Nie zwei Moves derselben VM gleichzeitig."""

import copy
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.restore import _StepCtx
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import SiteResolver, normalize_node_name, vhds_by_vm_uuid
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVVm
from app.models.restore_run import RestoreStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_move_run import VmMoveRun, VmMoveRunStep
from app.schemas.site import SiteBadge
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/vm-moves", tags=["vm-moves"])


class VmMoveRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class VmMoveRunRead(BaseModel):
    id: str
    hyperv_cluster_id: str
    vm_name: str
    move_type: str
    source_node: str | None = None
    target_node: str
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[VmMoveRunStepRead]

    class Config:
        from_attributes = True


class VmMoveRequest(BaseModel):
    cluster_id: str
    vm_name: str = Field(min_length=1)
    target_node: str = Field(min_length=1)


class MoveTargetNode(BaseModel):
    name: str
    state: str
    is_current: bool
    site: SiteBadge | None = None
    vm_count: int = 0


class MoveTargetsRead(BaseModel):
    vm_name: str
    # Owner-Knoten LIVE laut Cluster (nicht aus der Discovery).
    current_node: str | None = None
    host_site: SiteBadge | None = None
    storage_sites: list[SiteBadge] = []
    nodes: list[MoveTargetNode]
    # Gesetzt, wenn ein Move gerade nicht erlaubt ist (Backup laeuft etc.)
    # -- der Dialog zeigt den Grund und deaktiviert den Start-Button.
    blocked_reason: str | None = None


def _blocked_reason(db: Session, vm: HyperVVm) -> str | None:
    running_move = (
        db.query(VmMoveRun)
        .filter(VmMoveRun.hyperv_cluster_id == vm.cluster_id, VmMoveRun.vm_name == vm.name, VmMoveRun.status == RestoreStatus.RUNNING)
        .first()
    )
    if running_move is not None:
        return "Für diese VM läuft bereits eine Verschiebung."
    for run in db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CLEANING_UP])).all():
        if vm.name in (run.targets or []):
            return f"Backup-Lauf '{run.policy_name}' betrifft diese VM gerade -- bitte das Ende des Laufs abwarten."
    backup_checkpoints = [c["name"] for c in (vm.checkpoints or []) if str(c.get("name", "")).startswith("hvnb_")]
    if backup_checkpoints:
        return (
            f"Die VM hat einen offenen Backup-Checkpoint ({', '.join(backup_checkpoints)}). "
            "Erst den Checkpoint entfernen bzw. \"VM Discovery\" ausführen, falls er schon weg ist."
        )
    return None


def _cno_service(cluster: HyperVCluster, settings=None) -> HyperVService:
    return HyperVService(
        settings or get_settings(), cluster.management_address, use_https=cluster.use_https,
        node_hostname=cluster.hyperv_cluster_name,
    )


def _get_vm_or_404(db: Session, cluster_id: str, vm_name: str) -> HyperVVm:
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
    return vm


@router.get("/targets/{cluster_id}/{vm_name}", response_model=MoveTargetsRead)
def get_move_targets(
    cluster_id: str, vm_name: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> MoveTargetsRead:
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    vm = _get_vm_or_404(db, cluster_id, vm_name)

    password = decrypt_secret(cluster.encrypted_password)
    try:
        service = _cno_service(cluster)
        session = service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        nodes = service.list_cluster_nodes(session)
        current = service.get_vm_owner_node(session, vm_name) or vm.host_name
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cluster-Knoten konnten nicht abgefragt werden: {exc}") from exc

    resolver = SiteResolver(db)
    site_status = resolver.vm_status(vm, vhds_by_vm_uuid(db).get(vm.vm_uuid, []) if vm.vm_uuid else [])
    vm_counts: dict[str, int] = {}
    for other in db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id).all():
        key = normalize_node_name(other.host_name)
        vm_counts[key] = vm_counts.get(key, 0) + 1

    def _badge(site):
        return SiteBadge.model_validate(site) if site else None

    current_key = normalize_node_name(current)
    return MoveTargetsRead(
        vm_name=vm.name,
        current_node=current,
        # Host-Standort bezogen auf den LIVE-Owner (Discovery kann veraltet sein).
        host_site=_badge(resolver.node_site(cluster_id, current)),
        storage_sites=[SiteBadge.model_validate(s) for s in site_status.storage_sites],
        nodes=[
            MoveTargetNode(
                name=n.name, state=n.state, is_current=normalize_node_name(n.name) == current_key,
                site=_badge(resolver.node_site(cluster_id, n.name)),
                vm_count=vm_counts.get(normalize_node_name(n.name), 0),
            )
            for n in sorted(nodes, key=lambda n: n.name.lower())
        ],
        blocked_reason=_blocked_reason(db, vm),
    )


def _is_access_denied(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("access is denied", "zugriff verweigert", "0x80070005", "unauthorizedaccess"))


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="vm-move", message=message))
    db.commit()


def _execute_host_move(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(VmMoveRun, run_id)
        if run is None:
            return
        try:
            with _StepCtx(db, run.id, "connect", "Verbindung zum Cluster", step_model=VmMoveRunStep) as ctx:
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                if cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht gefunden")
                password = decrypt_secret(cluster.encrypted_password)
                settings = get_settings()
                service = _cno_service(cluster, settings)
                # Grosszuegige Timeouts: eine Live-Migration einer VM mit viel
                # RAM kann mehrere Minuten dauern. pywinrm pollt die Ausgabe
                # dabei in operation_timeout-Intervallen weiter, read_timeout
                # muss nur darueber liegen.
                session = service.connect(cluster.username, password, read_timeout_sec=90, operation_timeout_sec=60)
                ctx.row.message = f"{cluster.hyperv_cluster_name or cluster.management_address} (Transport {settings.winrm_transport})"

            with _StepCtx(db, run.id, "precheck", "Aktuellen Owner-Knoten ermitteln", step_model=VmMoveRunStep) as ctx:
                current = service.get_vm_owner_node(session, run.vm_name)
                if not current:
                    raise RuntimeError(f"VM '{run.vm_name}' hat keine Cluster-Rolle (nicht hochverfügbar) -- Live-Migration nicht möglich")
                run.source_node = current
                db.commit()
                if normalize_node_name(current) == normalize_node_name(run.target_node):
                    raise RuntimeError(f"VM läuft bereits auf '{current}'")
                ctx.row.message = f"Aktuell auf {current}"

            with _StepCtx(db, run.id, "migrate", f"Live-Migration {run.source_node} → {run.target_node}", step_model=VmMoveRunStep) as ctx:
                started = time.monotonic()
                transport_note = ""
                try:
                    new_owner = service.live_migrate_vm(session, run.vm_name, run.target_node)
                except RuntimeError as exc:
                    # Gleiches Double-Hop-Muster wie Add-ClusterVirtualMachineRole
                    # (siehe register-cluster-role in restore.py): ein Cluster-
                    # API-Aufruf aus einer Remote-Sitzung ohne Credential-
                    # Delegation scheitert mit 'Access is denied'. Nur dann
                    # einmalig gezielt per CredSSP wiederholen -- die Vorab-
                    # Checks im Skript laufen vor dem Move, ein abgelehnter
                    # Versuch hat also nichts veraendert.
                    if not _is_access_denied(exc) or settings.winrm_transport == "credssp":
                        raise
                    credssp_settings = copy.copy(settings)
                    credssp_settings.winrm_transport = "credssp"
                    credssp_service = _cno_service(cluster, credssp_settings)
                    credssp_session = credssp_service.connect(
                        cluster.username, password, read_timeout_sec=90, operation_timeout_sec=60,
                    )
                    new_owner = credssp_service.live_migrate_vm(credssp_session, run.vm_name, run.target_node)
                    transport_note = f" (per CredSSP, {settings.winrm_transport} wurde abgelehnt)"
                if normalize_node_name(new_owner) != normalize_node_name(run.target_node):
                    raise RuntimeError(f"Cluster meldet nach der Migration '{new_owner}' statt '{run.target_node}' als Owner")
                ctx.row.message = f"Jetzt auf {new_owner}, Dauer {round(time.monotonic() - started)} s{transport_note}"

            with _StepCtx(db, run.id, "update-inventory", "Inventory aktualisieren", step_model=VmMoveRunStep) as ctx:
                # Nur host_name nachziehen, keine Node-Abfrage noetig -- der
                # Cluster hat den neuen Owner oben bereits bestaetigt. Zeile
                # frisch laden (Discovery kann sie zwischenzeitlich neu
                # angelegt haben, siehe [[backup-vs-discovery-orm-race]]).
                vm = db.query(HyperVVm).filter(
                    HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name,
                ).first()
                if vm is not None:
                    vm.host_name = new_owner
                    db.commit()
                ctx.row.message = "OK"

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"VM '{run.vm_name}' per Live-Migration von {run.source_node} nach {run.target_node} verschoben (durch {run.requested_by})")
        except Exception as exc:
            db.rollback()
            run = db.get(VmMoveRun, run_id)
            run.status = RestoreStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"Verschieben von VM '{run.vm_name}' nach {run.target_node} fehlgeschlagen: {exc} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()


@router.post("", response_model=VmMoveRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_host_move(
    payload: VmMoveRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> VmMoveRun:
    if db.get(HyperVCluster, payload.cluster_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    vm = _get_vm_or_404(db, payload.cluster_id, payload.vm_name)
    reason = _blocked_reason(db, vm)
    if reason:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)
    run = VmMoveRun(
        hyperv_cluster_id=payload.cluster_id, vm_name=vm.name, vm_uuid=vm.vm_uuid, move_type="host",
        target_node=payload.target_node.strip(), requested_by=user.display_name or user.username,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_host_move, run.id)
    return run


@router.get("/{run_id}", response_model=VmMoveRunRead)
def get_move_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW)),
) -> VmMoveRun:
    run = db.get(VmMoveRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run
