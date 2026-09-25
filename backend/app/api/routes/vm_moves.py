"""VM verschieben ueber die App (Nutzer-Vorgabe 2026-09-25), als Aktion in
Inventory > VMs. Stufe 1: Host-Move per Live-Migration innerhalb des
Failover-Clusters. Stufe 2: Storage-Move per Move-VMStorage auf eine andere
CSV, Ordnerstruktur der Quelle bleibt erhalten (app.core.storage_move),
mit Fortschritt, Abbruch und Warnung bei geaendertem Backup-Schutz.

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
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.hyperv_clusters import _apply_vm_discovery_refresh, _get_vm_settled, _refresh_csv_rows
from app.api.routes.restore import _restore_settings, _StepCtx
from app.api.routes.vms import _annotate_vm
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import SiteResolver, normalize_node_name, vhds_by_vm_uuid
from app.core.storage_move import plan_storage_move, verify_storage_move
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.resource_group import ResourceGroup
from app.models.restore_run import RestoreStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_move_run import VmMoveRun, VmMoveRunStep
from app.schemas.site import SiteBadge
from app.schemas.vm import VmRead
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
    destination_csv_name: str | None = None
    progress_percent: int | None = None
    cancel_requested_at: datetime | None = None
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


# --- Stufe 2: Storage-Move ----------------------------------------------------


class StorageMoveRequest(BaseModel):
    cluster_id: str
    vm_name: str = Field(min_length=1)
    destination_csv_name: str = Field(min_length=1)
    # Pflicht, sobald sich der Backup-Schutz der VM durch den Move aendert
    # (protection_change != 'same') -- der Dialog zeigt die Warnung vorher.
    acknowledge_protection_change: bool = False


class StorageTargetCsv(BaseModel):
    name: str
    path: str | None = None
    capacity_bytes: int | None = None
    free_bytes: int | None = None
    site: SiteBadge | None = None
    # Alle Disks der VM liegen bereits auf dieser CSV.
    is_current: bool = False
    fits: bool = True
    protection_groups_after: list[str] = []
    # 'same' | 'changed' | 'lost' | 'gained'
    protection_change: str = "same"


class StorageTargetsRead(BaseModel):
    vm_name: str
    current_csvs: list[str]
    host_site: SiteBadge | None = None
    required_bytes: int
    protection_groups_now: list[str]
    csvs: list[StorageTargetCsv]
    blocked_reason: str | None = None


def _storage_blocked_reason(db: Session, vm: HyperVVm, vhds: list[HyperVVhd]) -> str | None:
    reason = _blocked_reason(db, vm)
    if reason:
        return reason
    if any(v.smb_server for v in vhds):
        return "Die VM liegt (teilweise) auf einer SMB3-Freigabe -- Storage-Move wird bisher nur zwischen CSVs unterstützt."
    if vm.checkpoints:
        return (
            f"Die VM hat {len(vm.checkpoints)} Checkpoint(s). Bitte vorher entfernen bzw. zusammenführen, "
            "damit die Differenzdateien nicht getrennt von ihrer Basis-VHDX verschoben werden."
        )
    if any((v.path or "").lower().endswith(".avhdx") for v in vhds):
        return "Mindestens eine Festplatte ist noch eine AVHDX-Differenzdatei -- bitte erst \"VM Discovery\" ausführen bzw. den Merge abwarten."
    if not vhds:
        return "Für diese VM sind keine Festplatten bekannt -- bitte zuerst eine Discovery ausführen."
    return None


def _protection_for(vm: HyperVVm, csv_names: list[str], groups: list[ResourceGroup]) -> list[str]:
    """Protection Groups, die die VM haette, wenn ihre Disks auf genau
    diesen CSVs laegen -- dieselbe Logik wie im Inventory (_annotate_vm:
    direkte VM-Mitgliedschaft ODER indirekt ueber eine CSV-Group)."""
    probe = VmRead(
        id=vm.id, name=vm.name, state=vm.state or "", host=vm.host_name or "", cluster_id=vm.cluster_id,
        csv_paths=[f"C:\\ClusterStorage\\{n}" for n in csv_names],
    )
    return _annotate_vm(probe, groups).resource_group_names


@router.get("/storage-targets/{cluster_id}/{vm_name}", response_model=StorageTargetsRead)
def get_storage_targets(
    cluster_id: str, vm_name: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> StorageTargetsRead:
    """Rein aus der DB (kein WinRM) -- die echten Ablageorte werden erst
    beim Start live gelesen und dort nochmals geprueft."""
    vm = _get_vm_or_404(db, cluster_id, vm_name)
    vhds = [v for v in db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id, HyperVVhd.vm_uuid == vm.vm_uuid).all()] if vm.vm_uuid else []
    current_csvs = sorted({v.csv_name for v in vhds if v.csv_name})
    groups = db.query(ResourceGroup).all()
    groups_now = _protection_for(vm, current_csvs, groups)
    resolver = SiteResolver(db)
    # Belegter Platz der Disks (Basis-VHDX bei AVHDX); bei dynamischen Disks
    # die tatsaechliche Dateigroesse, nicht die Maximalgroesse.
    required = sum((v.base_used_bytes or v.used_bytes or v.size_bytes or 0) for v in vhds)

    csvs: list[StorageTargetCsv] = []
    for csv in db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).order_by(HyperVCsv.name).all():
        is_current = bool(current_csvs) and current_csvs == [csv.name]
        free = (csv.capacity_bytes - csv.used_bytes) if csv.capacity_bytes is not None and csv.used_bytes is not None else None
        # Nur der Anteil, der tatsaechlich auf diese CSV wandert, zaehlt.
        needed = sum((v.base_used_bytes or v.used_bytes or v.size_bytes or 0) for v in vhds if v.csv_name != csv.name)
        groups_after = _protection_for(vm, [csv.name], groups)
        if set(groups_after) == set(groups_now):
            change = "same"
        elif not groups_after:
            change = "lost"
        elif not groups_now:
            change = "gained"
        else:
            change = "changed"
        site, _ = resolver.csv_site(csv)
        csvs.append(
            StorageTargetCsv(
                name=csv.name, path=csv.path, capacity_bytes=csv.capacity_bytes, free_bytes=free,
                site=SiteBadge.model_validate(site) if site else None, is_current=is_current,
                # 10 % Reserve -- eine randvoll gelaufene CSV legt alle VMs darauf lahm.
                fits=free is None or free - needed >= 0.1 * (csv.capacity_bytes or 0),
                protection_groups_after=groups_after, protection_change=change,
            )
        )

    host_site = resolver.node_site(cluster_id, vm.host_name)
    return StorageTargetsRead(
        vm_name=vm.name, current_csvs=current_csvs,
        host_site=SiteBadge.model_validate(host_site) if host_site else None,
        required_bytes=required, protection_groups_now=groups_now, csvs=csvs,
        blocked_reason=_storage_blocked_reason(db, vm, vhds),
    )


@router.post("/storage", response_model=VmMoveRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_storage_move(
    payload: StorageMoveRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> VmMoveRun:
    targets = get_storage_targets(payload.cluster_id, payload.vm_name, db, user)
    if targets.blocked_reason:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=targets.blocked_reason)
    destination = next((c for c in targets.csvs if c.name == payload.destination_csv_name), None)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"CSV '{payload.destination_csv_name}' nicht gefunden")
    if destination.is_current:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die VM liegt bereits vollständig auf dieser CSV")
    if not destination.fits:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Zu wenig freier Platz auf '{destination.name}' (10 % Reserve)")
    if destination.protection_change != "same" and not payload.acknowledge_protection_change:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Der Backup-Schutz der VM ändert sich durch diese Verschiebung -- bitte im Dialog bestätigen.",
        )
    vm = _get_vm_or_404(db, payload.cluster_id, payload.vm_name)
    run = VmMoveRun(
        hyperv_cluster_id=payload.cluster_id, vm_name=vm.name, vm_uuid=vm.vm_uuid, move_type="storage",
        # Storage-Moves laufen auf dem Owner-Knoten -- im Vorab-Check live
        # aktualisiert, hier nur der Discovery-Stand als Startwert.
        target_node=vm.host_name or "?", destination_csv_name=destination.name,
        requested_by=user.display_name or user.username, status=RestoreStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_storage_move, run.id)
    return run


@router.post("/{run_id}/cancel", response_model=VmMoveRunRead)
def cancel_move(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> VmMoveRun:
    run = db.get(VmMoveRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.move_type != "storage":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nur ein Storage-Move kann abgebrochen werden")
    if run.status != RestoreStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Lauf ist bereits beendet")
    if run.cancel_requested_at is None:
        run.cancel_requested_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return run


_PROGRESS_POLL_SEC = 10


def _execute_storage_move(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    try:
        run = db.get(VmMoveRun, run_id)
        if run is None:
            return
        try:
            with _StepCtx(db, run.id, "connect", "Verbindung zum Owner-Knoten", step_model=VmMoveRunStep) as ctx:
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                if cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht gefunden")
                dest_csv = db.query(HyperVCsv).filter(
                    HyperVCsv.cluster_id == run.hyperv_cluster_id, HyperVCsv.name == run.destination_csv_name,
                ).first()
                if dest_csv is None or not dest_csv.path:
                    raise RuntimeError(f"Ziel-CSV '{run.destination_csv_name}' (bzw. ihr Pfad) nicht gefunden -- Discovery ausführen")
                password = decrypt_secret(cluster.encrypted_password)
                # Kerberos -> NTLM wie bei Restore/Recreate: Aenderungen an den
                # Datentraegern einer geclusterten VM loesen intern ein
                # Cluster-Konfigurations-Update aus, das unter Kerberos ohne
                # Delegation scheitert (siehe _restore_settings).
                settings = _restore_settings()
                cno = _cno_service(cluster, settings)
                cno_session = cno.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
                owner = cno.get_vm_owner_node(cno_session, run.vm_name) or run.target_node
                node_address = cno.resolve_node_address(cno_session, owner)
                node = HyperVService(settings, node_address, use_https=cluster.use_https, node_hostname=owner)
                node_session = node.connect(cluster.username, password)
                run.source_node = owner
                run.target_node = owner
                db.commit()
                ctx.row.message = f"{owner} ({node_address}, Transport {settings.winrm_transport})"

            with _StepCtx(db, run.id, "plan", "Ablageorte lesen und Zielpfade planen", step_model=VmMoveRunStep) as ctx:
                layout = node.get_vm_storage_layout(node_session, run.vm_name)
                plan = plan_storage_move(layout, dest_csv.path)
                if plan.errors:
                    raise RuntimeError(" ".join(plan.errors))
                collisions = node.existing_paths(node_session, plan.collision_candidates)
                if collisions:
                    raise RuntimeError(f"Am Ziel existieren bereits: {', '.join(collisions)}")
                ctx.row.message = plan.summary()[:2000]

            with _StepCtx(db, run.id, "move", f"Dateien nach '{run.destination_csv_name}' verschieben", step_model=VmMoveRunStep) as ctx:
                started = time.monotonic()
                box: dict = {}

                def _worker() -> None:
                    # Eigene Sitzung fuer den blockierenden Aufruf -- die
                    # Fortschritts-Abfragen unten laufen parallel ueber
                    # node_session.
                    try:
                        move_session = node.connect(cluster.username, password, read_timeout_sec=90, operation_timeout_sec=60)
                        box["result"] = node.move_vm_storage(
                            move_session, run.vm_name,
                            virtual_machine_path=plan.virtual_machine_path, snapshot_file_path=plan.snapshot_file_path,
                            smart_paging_file_path=plan.smart_paging_file_path, vhd_moves=plan.vhd_moves,
                        )
                    except BaseException as exc:  # noqa: BLE001 -- unten ausgewertet
                        box["error"] = exc

                worker = threading.Thread(target=_worker, name=f"vm-storage-move-{run.id[:8]}", daemon=True)
                worker.start()
                cancel_sent = False
                while worker.is_alive():
                    worker.join(_PROGRESS_POLL_SEC)
                    if not worker.is_alive():
                        break
                    db.refresh(run)
                    if run.cancel_requested_at and not cancel_sent:
                        cancel_sent = True
                        try:
                            ok = node.cancel_storage_move(node_session, run.vm_uuid)
                            ctx.row.message = "Abbruch angefordert" if ok else "Abbruch angefordert, aber kein laufender Migrationsjob gefunden"
                        except Exception as exc:  # noqa: BLE001
                            ctx.row.message = f"Abbruch fehlgeschlagen: {exc}"
                        db.commit()
                        continue
                    try:
                        percent = node.storage_move_progress(node_session, run.vm_name, run.vm_uuid)
                    except Exception:  # noqa: BLE001 -- reine Anzeige
                        percent = None
                    if percent is not None and percent != run.progress_percent:
                        run.progress_percent = percent
                        if not cancel_sent:
                            ctx.row.message = f"{percent} %"
                        db.commit()

                command_error = str(box["error"]) if "error" in box else (
                    None if box["result"].success else (box["result"].error or "Move-VMStorage fehlgeschlagen")
                )
                # Entscheidend ist der tatsaechliche Zustand danach, nicht
                # der Rueckgabewert (siehe move_vm_storage-Docstring).
                verify_session = node.connect(cluster.username, password)
                after = node.get_vm_storage_layout(verify_session, run.vm_name)
                problems = verify_storage_move(after, plan)
                duration = round(time.monotonic() - started)
                if problems:
                    if cancel_sent:
                        raise RuntimeError(
                            "Abgebrochen -- die VM läuft weiter von den ursprünglichen Ablageorten "
                            f"({'; '.join(problems)})"
                        )
                    raise RuntimeError(f"{command_error or 'Verschiebung unvollständig'} -- {'; '.join(problems)}")
                run.progress_percent = 100
                note = f" (Move-VMStorage meldete trotzdem: {command_error})" if command_error else ""
                ctx.row.message = f"Alle Dateien auf '{run.destination_csv_name}', Dauer {duration // 60} min {duration % 60} s{note}"[:2000]

            with _StepCtx(db, run.id, "update-inventory", "Inventory aktualisieren", step_model=VmMoveRunStep) as ctx:
                # Best-effort: die Dateien liegen bereits am Ziel, ein Fehler
                # hier macht den Lauf nicht nachtraeglich zum Fehlschlag.
                try:
                    refreshed = _get_vm_settled(node, node_session, run.vm_name, username=cluster.username, password=password)
                    vm_fresh = db.query(HyperVVm).filter(
                        HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name,
                    ).first()
                    if refreshed is not None and vm_fresh is not None:
                        _apply_vm_discovery_refresh(db, run.hyperv_cluster_id, vm_fresh, refreshed)
                    _refresh_csv_rows(db, run.hyperv_cluster_id, cno.list_csvs(cno_session))
                    ctx.row.message = "VM und CSV-Belegung aktualisiert"
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    ctx.row.message = f"Aktualisierung fehlgeschlagen (Move war erfolgreich, nächste Discovery korrigiert es): {exc}"

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"Storage von VM '{run.vm_name}' nach CSV '{run.destination_csv_name}' verschoben (durch {run.requested_by})")
        except Exception as exc:
            db.rollback()
            run = db.get(VmMoveRun, run_id)
            run.status = RestoreStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db, f"Storage-Move von VM '{run.vm_name}' nach CSV '{run.destination_csv_name}' fehlgeschlagen: {exc} "
                f"(durch {run.requested_by})", level="ERROR",
            )
    finally:
        db.close()


@router.get("/{run_id}", response_model=VmMoveRunRead)
def get_move_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW)),
) -> VmMoveRun:
    run = db.get(VmMoveRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run
