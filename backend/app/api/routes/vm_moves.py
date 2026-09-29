"""VM verschieben ueber die App (Nutzer-Vorgabe 2026-09-25), als Aktion in
Inventory > VMs. Stufe 1: Host-Move per Live-Migration innerhalb des
Failover-Clusters. Stufe 2: Storage-Move per Move-VMStorage auf eine andere
CSV oder (seit 2026-09-29) von/zu einer SMB3-Freigabe, Ordnerstruktur der
Quelle bleibt erhalten (app.core.storage_move), mit Fortschritt, Abbruch und
Warnung bei geaendertem Backup-Schutz. Ist eine Freigabe beteiligt, laufen
Kollisionspruefung und Move ueber eine gezielte CredSSP-Sitzung (Double-Hop).

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.hyperv_clusters import (
    _apply_vm_discovery_refresh,
    _get_vm_settled,
    _refresh_csv_rows,
    _refresh_smb_share_rows,
)
from app.api.routes.restore import _restore_settings, _StepCtx
from app.api.routes.vms import _annotate_vm
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import SiteResolver, normalize_node_name, vhds_by_vm_uuid
from app.core.storage_move import is_unc, plan_storage_move, verify_storage_move
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVSmbShare, HyperVVhd, HyperVVm
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
    destination_smb_server: str | None = None
    destination_smb_share: str | None = None
    # CSV-Name oder \\\\server\\share
    destination_label: str | None = None
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
    # Live je Knoten abgefragt (None = Knoten nicht erreichbar/nicht Up).
    memory_total_bytes: int | None = None
    memory_free_bytes: int | None = None
    # Frei NACH dem Move (frei minus RAM der VM) -- nur fuer Zielkandidaten.
    memory_free_after_bytes: int | None = None
    # False = zu wenig RAM inkl. Reserve, None = unbekannt.
    fits_memory: bool | None = None
    memory_error: str | None = None
    recommended: bool = False


class MoveTargetsRead(BaseModel):
    vm_name: str
    # Owner-Knoten LIVE laut Cluster (nicht aus der Discovery).
    current_node: str | None = None
    host_site: SiteBadge | None = None
    storage_sites: list[SiteBadge] = []
    # RAM, den die VM auf dem Zielknoten braucht: bei laufender VM der
    # aktuell zugewiesene, sonst der Start-RAM (fuer den naechsten Start).
    vm_memory_bytes: int | None = None
    vm_state: str | None = None
    # Reserve, die auf dem Zielknoten nach dem Move frei bleiben muss.
    memory_reserve_bytes_hint: str = ""
    nodes: list[MoveTargetNode]
    recommended_node: str | None = None
    recommended_reason: str | None = None
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
    settings = get_settings()
    try:
        service = _cno_service(cluster, settings)
        session = service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        nodes = service.list_cluster_nodes(session)
        current = service.get_vm_owner_node(session, vm_name) or vm.host_name
        node_ips = service.node_address_map(session)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cluster-Knoten konnten nicht abgefragt werden: {exc}") from exc
    memory = _query_node_memory(cluster, password, settings, [n.name for n in nodes if n.state == "Up"], node_ips, current, vm_name)

    resolver = SiteResolver(db)
    site_status = resolver.vm_status(vm, vhds_by_vm_uuid(db).get(vm.vm_uuid, []) if vm.vm_uuid else [])
    vm_counts: dict[str, int] = {}
    for other in db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id).all():
        key = normalize_node_name(other.host_name)
        vm_counts[key] = vm_counts.get(key, 0) + 1

    def _badge(site):
        return SiteBadge.model_validate(site) if site else None

    current_key = normalize_node_name(current)
    vm_need = memory.vm_need_bytes
    target_nodes: list[MoveTargetNode] = []
    for n in sorted(nodes, key=lambda n: n.name.lower()):
        key = normalize_node_name(n.name)
        info = memory.nodes.get(key)
        node = MoveTargetNode(
            name=n.name, state=n.state, is_current=key == current_key,
            site=_badge(resolver.node_site(cluster_id, n.name)),
            vm_count=vm_counts.get(key, 0),
            memory_error=memory.errors.get(key),
        )
        if info is not None:
            node.memory_total_bytes, node.memory_free_bytes = info
            if not node.is_current and vm_need is not None:
                node.memory_free_after_bytes = node.memory_free_bytes - vm_need
                node.fits_memory = node.memory_free_after_bytes >= _memory_reserve(node.memory_total_bytes)
        target_nodes.append(node)

    storage_site_ids = {s.id for s in site_status.storage_sites}
    recommended, reason = _recommend_node(target_nodes, storage_site_ids)
    for node in target_nodes:
        node.recommended = node.name == recommended

    return MoveTargetsRead(
        vm_name=vm.name,
        current_node=current,
        # Host-Standort bezogen auf den LIVE-Owner (Discovery kann veraltet sein).
        host_site=_badge(resolver.node_site(cluster_id, current)),
        storage_sites=[SiteBadge.model_validate(s) for s in site_status.storage_sites],
        vm_memory_bytes=vm_need,
        vm_state=memory.vm_state,
        memory_reserve_bytes_hint=f"{_RESERVE_PERCENT} % des Knoten-RAMs, mindestens {_RESERVE_MIN_BYTES // 2**30} GB",
        nodes=target_nodes,
        recommended_node=recommended,
        recommended_reason=reason,
        blocked_reason=_blocked_reason(db, vm),
    )


# Auf dem Zielknoten muss nach dem Move mindestens so viel RAM frei bleiben
# -- der Hyper-V-Host selbst (Root-Partition, Cluster-Dienst, Treiber)
# braucht Luft, und ein Failover eines anderen Knotens soll nicht sofort an
# vollgelaufenem Speicher scheitern.
_RESERVE_PERCENT = 10
_RESERVE_MIN_BYTES = 4 * 2**30


def _memory_reserve(total_bytes: int) -> int:
    return max(_RESERVE_MIN_BYTES, total_bytes * _RESERVE_PERCENT // 100)


@dataclass
class _MemorySnapshot:
    nodes: dict[str, tuple[int, int]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    vm_need_bytes: int | None = None
    vm_state: str | None = None


def _query_node_memory(
    cluster: HyperVCluster, password: str, settings, node_names: list[str], node_ips: dict[str, str],
    owner: str | None, vm_name: str,
) -> _MemorySnapshot:
    """Fragt je Knoten (parallel, direkte Verbindung zur Management-IP wie
    beim Backup) den freien RAM ab, auf dem Owner-Knoten zusaetzlich den RAM
    der VM. Ein nicht erreichbarer Knoten verhindert den Dialog nicht --
    er bekommt nur keine RAM-Angabe (fits_memory = None)."""
    snapshot = _MemorySnapshot()
    owner_key = normalize_node_name(owner)

    def _one(name: str):
        node = HyperVService(settings, node_ips.get(name.lower(), name), use_https=cluster.use_https, node_hostname=name)
        session = node.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        mem = node.node_memory(session)
        vm_info = node.vm_memory(session, vm_name) if normalize_node_name(name) == owner_key else None
        return mem, vm_info

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(node_names)))) as pool:
        futures = {pool.submit(_one, name): name for name in node_names}
        for future, name in futures.items():
            key = normalize_node_name(name)
            try:
                mem, vm_info = future.result(timeout=40)
            except Exception as exc:  # noqa: BLE001 -- nur Anzeige
                snapshot.errors[key] = str(exc)[:300]
                continue
            snapshot.nodes[key] = mem
            if vm_info is not None:
                state, assigned, startup = vm_info
                snapshot.vm_state = state
                snapshot.vm_need_bytes = assigned if state == "Running" and assigned else startup
    return snapshot


def _recommend_node(nodes: list[MoveTargetNode], storage_site_ids: set[str]) -> tuple[str | None, str | None]:
    """Empfohlener Zielknoten: Up, nicht der aktuelle, genug RAM (oder RAM
    unbekannt), und -- falls der Storage-Standort bekannt ist -- NUR am
    Standort des Storage (sonst wuerde der Move eine Standort-Abweichung
    erzeugen statt beheben). Darunter der mit dem meisten freien RAM nach
    dem Move; Knoten mit bekanntem RAM vor solchen ohne."""
    candidates = [n for n in nodes if n.state == "Up" and not n.is_current and n.fits_memory is not False]
    if storage_site_ids:
        same_site = [n for n in candidates if n.site and n.site.id in storage_site_ids]
        if not same_site:
            return None, "Kein betriebsbereiter Knoten mit genug freiem RAM am Standort des Storage."
        candidates = same_site
    if not candidates:
        return None, "Kein betriebsbereiter Knoten mit genug freiem RAM."
    best = max(candidates, key=lambda n: (n.memory_free_after_bytes is not None, n.memory_free_after_bytes or 0, -n.vm_count))
    parts = []
    if storage_site_ids and best.site:
        parts.append(f"gleicher Standort wie der Storage ({best.site.name})")
    if best.memory_free_after_bytes is not None:
        parts.append(f"meisten freien RAM nach dem Move ({best.memory_free_after_bytes / 2**30:.0f} GB)")
    return best.name, ", ".join(parts) or None


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
    # Name des Ziels aus StorageTargetsRead.csvs: CSV-Name oder, fuer eine
    # SMB3-Freigabe, der UNC-Pfad \\\\server\\share.
    destination_name: str = Field(min_length=1)
    # Pflicht, sobald sich der Backup-Schutz der VM durch den Move aendert
    # (protection_change != 'same') -- der Dialog zeigt die Warnung vorher.
    acknowledge_protection_change: bool = False


class StorageTargetCsv(BaseModel):
    """Ein moegliches Storage-Ziel -- trotz des (historischen) Namens auch
    eine SMB3-Freigabe (kind='smb', name = \\\\server\\share)."""

    kind: str = "csv"
    name: str
    path: str | None = None
    smb_server: str | None = None
    smb_share: str | None = None
    capacity_bytes: int | None = None
    free_bytes: int | None = None
    # Platz, den die VM auf DIESER CSV zusaetzlich belegen wuerde, wenn sie
    # das Ziel ist (ohne Disks, die schon hier liegen).
    needed_bytes: int = 0
    # Frei, wenn diese CSV das Ziel ist (frei minus needed_bytes) -- analog
    # zu memory_free_after_bytes beim Host-Move.
    free_after_bytes: int | None = None
    # Platz, den die VM hier heute belegt und der beim Wegverschieben frei
    # wird (nur CSVs, auf denen die VM aktuell liegt).
    freed_bytes: int = 0
    site: SiteBadge | None = None
    # Alle Disks der VM liegen bereits auf dieser CSV.
    is_current: bool = False
    fits: bool = True
    protection_groups_after: list[str] = []
    # 'same' | 'changed' | 'lost' | 'gained'
    protection_change: str = "same"


class StorageTargetsRead(BaseModel):
    vm_name: str
    # Aktuelle Ablageorte: CSV-Namen und/oder \\\\server\\share
    current_csvs: list[str]
    host_site: SiteBadge | None = None
    required_bytes: int
    protection_groups_now: list[str]
    csvs: list[StorageTargetCsv]
    # True = Belegung eben live vom Cluster gelesen, False = Stand der
    # letzten Discovery (Live-Abfrage fehlgeschlagen, siehe usage_note).
    usage_live: bool = False
    usage_note: str | None = None
    reserve_hint: str = ""
    recommended_csv: str | None = None
    recommended_reason: str | None = None
    blocked_reason: str | None = None


def _storage_blocked_reason(db: Session, vm: HyperVVm, vhds: list[HyperVVhd]) -> str | None:
    reason = _blocked_reason(db, vm)
    if reason:
        return reason
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


def _protection_for(vm: HyperVVm, locations: list[str], groups: list[ResourceGroup]) -> list[str]:
    """Protection Groups, die die VM haette, wenn ihre Disks an genau diesen
    Orten laegen (CSV-Namen und/oder \\\\server\\share) -- dieselbe Logik wie
    im Inventory (_annotate_vm: direkte VM-Mitgliedschaft ODER indirekt
    ueber eine CSV- bzw. SMB3-Group)."""
    probe = VmRead(
        id=vm.id, name=vm.name, state=vm.state or "", host=vm.host_name or "", cluster_id=vm.cluster_id,
        csv_paths=[f"C:\\ClusterStorage\\{n}" for n in locations if not is_unc(n)],
        smb_share_paths=[n for n in locations if is_unc(n)],
    )
    return _annotate_vm(probe, groups).resource_group_names


@router.get("/storage-targets/{cluster_id}/{vm_name}", response_model=StorageTargetsRead)
def get_storage_targets(
    cluster_id: str, vm_name: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> StorageTargetsRead:
    """Belegung der CSVs live vom Cluster (ein Get-ClusterSharedVolume-
    Aufruf gegen den CNO, gleiche Quelle wie die Discovery), faellt bei
    einem Fehler auf den Discovery-Stand zurueck. Die echten Ablageorte der
    VM werden erst beim Start live gelesen und dort nochmals geprueft."""
    vm = _get_vm_or_404(db, cluster_id, vm_name)
    live_usage, usage_note = _live_csv_usage(db, cluster_id)
    vhds = [v for v in db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id, HyperVVhd.vm_uuid == vm.vm_uuid).all()] if vm.vm_uuid else []
    current = sorted({_vhd_location(v) for v in vhds if _vhd_location(v)})
    current_keys = {c.lower() for c in current}
    groups = db.query(ResourceGroup).all()
    groups_now = _protection_for(vm, current, groups)
    resolver = SiteResolver(db)
    # Belegter Platz der Disks (Basis-VHDX bei AVHDX); bei dynamischen Disks
    # die tatsaechliche Dateigroesse, nicht die Maximalgroesse.
    required = sum(_vhd_bytes(v) for v in vhds)

    def _target(kind: str, name: str, capacity: int | None, used: int | None, site, **extra) -> StorageTargetCsv:
        key = name.lower()
        is_current = current_keys == {key}
        free = (capacity - used) if capacity is not None and used is not None else None
        # Nur der Anteil, der tatsaechlich an dieses Ziel wandert, zaehlt.
        needed = sum(_vhd_bytes(v) for v in vhds if (_vhd_location(v) or "").lower() != key)
        freed = sum(_vhd_bytes(v) for v in vhds if (_vhd_location(v) or "").lower() == key)
        groups_after = _protection_for(vm, [name], groups)
        if set(groups_after) == set(groups_now):
            change = "same"
        elif not groups_after:
            change = "lost"
        elif not groups_now:
            change = "gained"
        else:
            change = "changed"
        return StorageTargetCsv(
            kind=kind, name=name, capacity_bytes=capacity, free_bytes=free, needed_bytes=needed, freed_bytes=freed,
            free_after_bytes=(free - needed) if free is not None and not is_current else None,
            site=SiteBadge.model_validate(site) if site else None, is_current=is_current,
            # Reserve -- ein randvoll gelaufener Speicher legt alle VMs darauf lahm.
            fits=free is None or free - needed >= _CSV_RESERVE_PERCENT * (capacity or 0) // 100,
            protection_groups_after=groups_after, protection_change=change, **extra,
        )

    csvs: list[StorageTargetCsv] = []
    for csv in db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).order_by(HyperVCsv.name).all():
        capacity, used = live_usage.get(csv.name, (csv.capacity_bytes, csv.used_bytes))
        site, _ = resolver.csv_site(csv)
        csvs.append(_target("csv", csv.name, capacity, used, site, path=csv.path))
    # SMB3-Freigaben, die dieser Cluster bereits nutzt (Discovery-Stand; die
    # Groesse ist die des NetApp-Volumes darunter, siehe _refresh_smb_share_rows).
    for share in db.query(HyperVSmbShare).filter(HyperVSmbShare.cluster_id == cluster_id).order_by(HyperVSmbShare.server, HyperVSmbShare.share).all():
        unc = f"\\\\{share.server}\\{share.share}"
        csvs.append(
            _target(
                "smb", unc, share.capacity_bytes, share.used_bytes, resolver.smb_share_site(share),
                path=unc, smb_server=share.server, smb_share=share.share,
            )
        )

    host_site = resolver.node_site(cluster_id, vm.host_name)
    recommended, reason = _recommend_csv(csvs, host_site.id if host_site else None)
    return StorageTargetsRead(
        vm_name=vm.name, current_csvs=current,
        host_site=SiteBadge.model_validate(host_site) if host_site else None,
        required_bytes=required, protection_groups_now=groups_now, csvs=csvs,
        usage_live=bool(live_usage), usage_note=usage_note,
        reserve_hint=f"{_CSV_RESERVE_PERCENT} % der Kapazität",
        recommended_csv=recommended, recommended_reason=reason,
        blocked_reason=_storage_blocked_reason(db, vm, vhds),
    )


def _vhd_location(vhd: HyperVVhd) -> str | None:
    """Ablageort einer VHD als Ziel-Name: CSV-Name oder \\\\server\\share."""
    if vhd.csv_name:
        return vhd.csv_name
    if vhd.smb_server and vhd.smb_share:
        return f"\\\\{vhd.smb_server}\\{vhd.smb_share}"
    return None


def _vhd_bytes(vhd: HyperVVhd) -> int:
    return vhd.base_used_bytes or vhd.used_bytes or vhd.size_bytes or 0


_CSV_RESERVE_PERCENT = 10


def _live_csv_usage(db: Session, cluster_id: str) -> tuple[dict[str, tuple[int, int]], str | None]:
    """{CSV-Name: (Kapazitaet, belegt)} live vom Cluster -- wird NICHT in die
    DB geschrieben (ein GET soll keine Discovery-Zeilen ersetzen). Leeres
    Dict + Hinweis, wenn die Abfrage scheitert."""
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        return {}, None
    try:
        service = _cno_service(cluster)
        session = service.connect(
            cluster.username, decrypt_secret(cluster.encrypted_password), read_timeout_sec=15, operation_timeout_sec=10,
        )
        return {c.name: (c.capacity_bytes, c.used_bytes) for c in service.list_csvs(session)}, None
    except Exception as exc:  # noqa: BLE001 -- nur Anzeige, Fallback auf Discovery-Stand
        return {}, f"Live-Abfrage fehlgeschlagen, Stand der letzten Discovery ({str(exc)[:200]})"


def _recommend_csv(csvs: list[StorageTargetCsv], host_site_id: str | None) -> tuple[str | None, str | None]:
    """Empfohlenes Ziel (CSV oder SMB3-Freigabe): nicht das aktuelle, genug
    Platz, und -- falls der Host-Standort bekannt ist -- NUR am Standort des
    Hosts. Darunter zuerst eines, bei dem der Backup-Schutz gleich bleibt,
    dann eines mit anderem Schutz, zuletzt eines ohne; bei Gleichstand das
    mit dem meisten freien Platz."""
    candidates = [c for c in csvs if not c.is_current and c.fits]
    if host_site_id:
        candidates = [c for c in candidates if c.site and c.site.id == host_site_id]
        if not candidates:
            return None, "Kein Ziel mit genug Platz am Standort des Hosts."
    if not candidates:
        return None, "Kein Ziel mit genug freiem Platz."
    # Schutz bleibt/kommt hinzu vor "anderes Profil" vor "danach ungeschuetzt"
    # -- sonst gewann bei lauter Schutzwechseln einfach das Ziel mit dem
    # meisten Platz, auch wenn die VM dort ungeschuetzt waere.
    protection_rank = {"same": 2, "gained": 2, "changed": 1, "lost": 0}
    best = max(candidates, key=lambda c: (protection_rank.get(c.protection_change, 0), c.free_after_bytes or 0))
    parts = []
    if host_site_id and best.site:
        parts.append(f"gleicher Standort wie der Host ({best.site.name})")
    if best.protection_change == "same":
        parts.append("Backup-Schutz bleibt gleich")
    if best.free_after_bytes is not None:
        parts.append(f"meisten freien Platz nach dem Move ({best.free_after_bytes / 2**30:.0f} GB)")
    return best.name, ", ".join(parts) or None


@router.post("/storage", response_model=VmMoveRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_storage_move(
    payload: StorageMoveRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> VmMoveRun:
    targets = get_storage_targets(payload.cluster_id, payload.vm_name, db, user)
    if targets.blocked_reason:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=targets.blocked_reason)
    destination = next((c for c in targets.csvs if c.name.lower() == payload.destination_name.lower()), None)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Ziel '{payload.destination_name}' nicht gefunden")
    if destination.is_current:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die VM liegt bereits vollständig an diesem Ziel")
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
        target_node=vm.host_name or "?",
        destination_csv_name=destination.name if destination.kind == "csv" else None,
        destination_smb_server=destination.smb_server if destination.kind == "smb" else None,
        destination_smb_share=destination.smb_share if destination.kind == "smb" else None,
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
                if run.destination_smb_server and run.destination_smb_share:
                    destination_root = run.destination_label
                else:
                    dest_csv = db.query(HyperVCsv).filter(
                        HyperVCsv.cluster_id == run.hyperv_cluster_id, HyperVCsv.name == run.destination_csv_name,
                    ).first()
                    if dest_csv is None or not dest_csv.path:
                        raise RuntimeError(f"Ziel-CSV '{run.destination_csv_name}' (bzw. ihr Pfad) nicht gefunden -- Discovery ausführen")
                    destination_root = dest_csv.path
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
                plan = plan_storage_move(layout, destination_root)
                if plan.errors:
                    raise RuntimeError(" ".join(plan.errors))
                # Liegt Quelle oder Ziel auf einer SMB3-Freigabe, greift Hyper-V
                # (VMMS) im Namen des Aufrufers auf die Freigabe zu -- ein echter
                # Double-Hop, den weder NTLM noch Kerberos ohne Delegation
                # erlauben. Wie beim SMB3-Restore (_execute_smb_restore_add)
                # deshalb gezielt eine CredSSP-Sitzung NUR fuer Kollisions-
                # pruefung und Move; Fortschritt/Abbruch laufen lokal per WMI.
                touched = [p for pair in plan.vhd_moves for p in pair] + [
                    p for p in (
                        layout.configuration_location, layout.snapshot_file_location, layout.smart_paging_file_path,
                        plan.virtual_machine_path, plan.snapshot_file_path, plan.smart_paging_file_path,
                    ) if p
                ]
                uses_smb = any(is_unc(p) for p in touched)
                if uses_smb:
                    credssp_settings = copy.copy(settings)
                    credssp_settings.winrm_transport = "credssp"
                    move_service = HyperVService(credssp_settings, node_address, use_https=cluster.use_https, node_hostname=owner)
                else:
                    move_service = node
                collisions = move_service.existing_paths(
                    move_service.connect(cluster.username, password) if uses_smb else node_session, plan.collision_candidates,
                )
                if collisions:
                    raise RuntimeError(f"Am Ziel existieren bereits: {', '.join(collisions)}")
                ctx.row.message = (plan.summary() + (" (SMB3: CredSSP)" if uses_smb else ""))[:2000]

            with _StepCtx(db, run.id, "move", f"Dateien nach '{run.destination_label}' verschieben", step_model=VmMoveRunStep) as ctx:
                started = time.monotonic()
                box: dict = {}

                def _worker() -> None:
                    # Eigene Sitzung fuer den blockierenden Aufruf -- die
                    # Fortschritts-Abfragen unten laufen parallel ueber
                    # node_session.
                    try:
                        move_session = move_service.connect(cluster.username, password, read_timeout_sec=90, operation_timeout_sec=60)
                        box["result"] = move_service.move_vm_storage(
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
                ctx.row.message = f"Alle Dateien auf '{run.destination_label}', Dauer {duration // 60} min {duration % 60} s{note}"[:2000]

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
                    # SMB3-Freigaben werden aus den VHD-Zeilen des ganzen
                    # Clusters abgeleitet -- nach dem Move ggf. eine neue
                    # Freigabe bzw. eine, die keine VM mehr traegt.
                    _refresh_smb_share_rows(
                        db, run.hyperv_cluster_id,
                        db.query(HyperVVhd).filter(HyperVVhd.cluster_id == run.hyperv_cluster_id).all(),
                    )
                    ctx.row.message = "VM, CSV- und SMB3-Belegung aktualisiert"
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    ctx.row.message = f"Aktualisierung fehlgeschlagen (Move war erfolgreich, nächste Discovery korrigiert es): {exc}"

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"Storage von VM '{run.vm_name}' nach '{run.destination_label}' verschoben (durch {run.requested_by})")
        except Exception as exc:
            db.rollback()
            run = db.get(VmMoveRun, run_id)
            run.status = RestoreStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db, f"Storage-Move von VM '{run.vm_name}' nach '{run.destination_label}' fehlgeschlagen: {exc} "
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
