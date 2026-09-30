"""SMB3-Freigabe loeschen (Nutzer-Vorgaben 2026-09-30), Gegenstueck zu
app.api.routes.csv_delete mit denselben Regeln:

- Gesperrt, solange laut Inventory VMs auf der Freigabe liegen oder VM-
  Dateien darauf gefunden werden. Der Dateiscan laeuft ueber die ONTAP-
  Datei-API (kein SMB-Zugriff, also kein Double-Hop).
- Gesperrt, solange das Volume SnapMirror-Quelle ist.
- Andere Dateien und die mitgeloeschten Backup-Snapshots als Warnung;
  Backup-Eintraege werden danach als nicht mehr vorhanden markiert.
- Automatisch aus allen Protection Groups ausgetragen.
- Volume ist Opt-out und nur loeschbar, wenn keine weitere Freigabe und
  keine LUN darin liegt.
- Bestaetigung durch Eintippen des Freigabenamens.

Ablauf: Vorpruefung -> Freigabe loeschen -> aus Protection Groups austragen
-> Volume loeschen (optional) -> Inventory. Kein Zurueckrollen."""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.csv_resize import _require_hyperv_manage
from app.api.routes.hyperv_clusters import _refresh_smb_share_rows
from app.api.routes.jobs import _smb_share_key
from app.api.routes.netapp_clusters import _discover_and_persist
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.restore import _StepCtx
from app.api.routes.smb_resize import _cifs_share_for
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, BackupRunSnapshot, JobStatus
from app.models.hyperv_discovery import HyperVSmbShare, HyperVVhd
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSnapMirrorRelationship, NetAppVolume
from app.models.resource_group import ResourceGroup, parse_member_key
from app.models.restore_run import RestoreStatus
from app.models.smb_share_run import SmbCreateRun, SmbDeleteRun, SmbDeleteRunStep
from app.models.system_log import SystemLogEvent
from app.models.vm_move_run import VmMoveRun
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/smb-delete", tags=["smb-delete"])

GIB = 1024**3


class FileEntry(BaseModel):
    path: str
    size_bytes: int


class SnapshotSummary(BaseModel):
    total: int
    backup_count: int
    oldest_backup: str | None = None
    newest_backup: str | None = None


class SmbDeleteInfo(BaseModel):
    cluster_id: str
    server: str
    share: str
    share_path: str | None = None
    netapp_cluster_id: str
    netapp_cluster_name: str
    svm_name: str
    volume_uuid: str
    volume_name: str
    volume_size_bytes: int
    used_bytes: int | None = None
    other_shares: list[str]
    lun_count: int
    volume_deletable: bool
    snapshots: SnapshotSummary
    snapmirror_destinations: list[str]
    vms: list[str]
    vm_files: list[FileEntry]
    other_files: list[FileEntry]
    files_truncated: bool
    protection_groups: list[str]
    blocked_reasons: list[str]
    warnings: list[str]


class SmbDeleteRequest(BaseModel):
    cluster_id: str
    server: str
    share: str
    confirm_name: str
    delete_volume: bool = True


class StepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class SmbDeleteRunRead(BaseModel):
    id: str
    server: str
    share: str
    volume_name: str
    delete_volume: bool
    capacity_bytes: int | None = None
    removed_from_groups: list[str] = []
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[StepRead]

    class Config:
        from_attributes = True


def _gb(value: int | None) -> str:
    return f"{(value or 0) / GIB:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="storage", message=message))
    db.commit()


def _is_share_member(member: str, cluster_id: str, server: str, share: str) -> bool:
    member_cluster, name = parse_member_key(member)
    return member_cluster in (cluster_id, None) and name.lower() == _smb_share_key(server, share).lower()


def _groups_with_share(db: Session, cluster_id: str, server: str, share: str) -> list[ResourceGroup]:
    return [
        g for g in db.query(ResourceGroup).all()
        if any(_is_share_member(m, cluster_id, server, share) for m in (g.members or []))
    ]


def _busy_reasons(db: Session, cluster_id: str, server: str, share: str, own_run_id: str | None = None) -> list[str]:
    reasons = []
    group_ids = {g.id for g in _groups_with_share(db, cluster_id, server, share)}
    for run in db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CLEANING_UP])).all():
        if run.resource_group_id in group_ids:
            reasons.append(f"Backup-Lauf '{run.policy_name}' sichert diese Freigabe gerade.")
    for move in db.query(VmMoveRun).filter(VmMoveRun.hyperv_cluster_id == cluster_id, VmMoveRun.status == RestoreStatus.RUNNING):
        if (move.destination_smb_server or "").lower() == server.lower() and (move.destination_smb_share or "").lower() == share.lower():
            reasons.append("Gerade wird eine VM auf diese Freigabe verschoben.")
    if db.query(SmbCreateRun).filter(SmbCreateRun.hyperv_cluster_id == cluster_id, SmbCreateRun.status == RestoreStatus.RUNNING).first():
        reasons.append("Auf diesem Cluster wird gerade eine SMB3-Freigabe angelegt.")
    for other in db.query(SmbDeleteRun).filter(
        SmbDeleteRun.hyperv_cluster_id == cluster_id, SmbDeleteRun.status == RestoreStatus.RUNNING, SmbDeleteRun.id != (own_run_id or ""),
    ):
        if other.server.lower() == server.lower() and other.share.lower() == share.lower():
            reasons.append("Diese Freigabe wird bereits gelöscht.")
    return reasons


def _volume_relative(share_path: str | None, junction: str | None) -> str:
    """Freigabepfad (Namespace der SVM) -> Pfad innerhalb des Volumes."""
    path = (share_path or "/").replace("\\", "/").rstrip("/")
    root = (junction or "").rstrip("/")
    if root and path.lower().startswith(root.lower()):
        path = path[len(root):]
    return path or "/"


def load_info(db: Session, cluster_id: str, server: str, share: str, own_run_id: str | None = None) -> SmbDeleteInfo:  # noqa: C901
    row = db.query(HyperVSmbShare).filter(
        HyperVSmbShare.cluster_id == cluster_id, HyperVSmbShare.server == server, HyperVSmbShare.share == share,
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SMB3-Freigabe nicht gefunden")
    cifs = _cifs_share_for(db, server, share)
    if cifs is None or not cifs.volume_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Die Freigabe ist keinem NetApp-Volume zugeordnet -- NetApp-System registriert und discovert?",
        )
    netapp_cluster = db.get(NetAppCluster, cifs.cluster_id)
    volume_row = db.query(NetAppVolume).filter(
        NetAppVolume.cluster_id == cifs.cluster_id, NetAppVolume.svm_name == cifs.svm_name, NetAppVolume.name == cifs.volume_name,
    ).first()
    if netapp_cluster is None or volume_row is None or not volume_row.uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Volume '{cifs.volume_name}' ist nicht bekannt -- bitte Discovery ausführen.",
        )

    blocked = _busy_reasons(db, cluster_id, server, share, own_run_id)
    warnings: list[str] = []
    netapp = _netapp_service_for(netapp_cluster)
    try:
        volume = netapp.volume_space(volume_row.uuid)
        shares_on_volume = netapp.cifs_shares_on_volume(volume_row.uuid)
        snapshots = netapp.list_snapshots(volume_row.uuid)
        destinations = netapp.snapmirror_destinations_of(cifs.svm_name, volume["name"])
        scan = netapp.volume_file_scan(volume_row.uuid, _volume_relative(cifs.path, volume["junction_path"]))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NetApp-Abfrage fehlgeschlagen: {exc}") from exc
    for rel in db.query(NetAppSnapMirrorRelationship).filter(NetAppSnapMirrorRelationship.source_path == f"{cifs.svm_name}:{volume['name']}"):
        if rel.destination_path and rel.destination_path not in destinations:
            destinations.append(rel.destination_path)

    vms = sorted({
        v.vm_name for v in db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id)
        if v.vm_name and (v.smb_server or "").lower() == server.lower() and (v.smb_share or "").lower() == share.lower()
    })
    vm_files = [f for f in scan["files"] if f["path"].lower().endswith(HyperVService._VM_FILE_EXTENSIONS)]
    other_files = [f for f in scan["files"] if f not in vm_files]
    if vms:
        blocked.append(f"Auf der Freigabe liegen laut Inventory noch VMs: {', '.join(vms)}.")
    if vm_files:
        blocked.append(f"Auf der Freigabe liegen noch {len(vm_files)} VM-Datei(en) (VHDX/Konfiguration).")
    if destinations:
        blocked.append(
            f"Das Volume {volume['name']} ist Quelle einer SnapMirror-Beziehung ({', '.join(destinations)}) -- "
            "bitte die Beziehung zuerst in Storage auflösen. Die Ziel-Volumes bleiben dabei erhalten."
        )
    if other_files:
        warnings.append(
            f"Auf der Freigabe liegen noch {len(other_files)}{'+' if scan['truncated'] else ''} andere Datei(en) -- "
            "sie gehen verloren, wenn das Volume gelöscht wird."
        )

    other_shares = [s for s in shares_on_volume if s.lower() != share.lower()]
    lun_count = len(volume["luns"])
    volume_deletable = not other_shares and lun_count == 0
    if other_shares:
        warnings.append(f"Auf dem Volume liegen weitere Freigaben ({', '.join(other_shares)}) -- das Volume bleibt stehen.")
    if lun_count:
        warnings.append(f"Im Volume liegen {lun_count} LUN(s) -- das Volume bleibt stehen.")

    backup_names = {
        r.snapshot_name for r in db.query(BackupRunSnapshot).filter(
            BackupRunSnapshot.volume_uuid == volume_row.uuid, BackupRunSnapshot.success.is_(True),
        ) if r.snapshot_name
    }
    backup_times = sorted(s["create_time"] for s in snapshots if s["name"] in backup_names and s["create_time"])
    summary = SnapshotSummary(
        total=len(snapshots), backup_count=len([s for s in snapshots if s["name"] in backup_names]),
        oldest_backup=backup_times[0] if backup_times else None, newest_backup=backup_times[-1] if backup_times else None,
    )
    if volume_deletable and summary.total:
        warnings.append(
            f"Wird das Volume mit gelöscht, verschwinden {summary.total} Snapshot(s), davon {summary.backup_count} Backup(s) "
            "dieser App -- diese Backups sind danach nicht mehr wiederherstellbar (Kopien auf SnapMirror-Zielen bleiben)."
        )
    groups = [g.name for g in _groups_with_share(db, cluster_id, server, share)]
    if groups:
        warnings.append(f"Die Freigabe wird aus den Protection Groups {', '.join(groups)} ausgetragen.")

    return SmbDeleteInfo(
        cluster_id=cluster_id, server=server, share=share, share_path=cifs.path, netapp_cluster_id=netapp_cluster.id,
        netapp_cluster_name=netapp_cluster.name, svm_name=cifs.svm_name, volume_uuid=volume_row.uuid, volume_name=volume["name"],
        volume_size_bytes=volume["size_bytes"] or 0, used_bytes=volume["used_bytes"], other_shares=other_shares,
        lun_count=lun_count, volume_deletable=volume_deletable, snapshots=summary, snapmirror_destinations=destinations,
        vms=vms, vm_files=[FileEntry(**f) for f in vm_files], other_files=[FileEntry(**f) for f in other_files],
        files_truncated=scan["truncated"], protection_groups=groups, blocked_reasons=blocked, warnings=warnings,
    )


@router.get("/runs/{run_id}", response_model=SmbDeleteRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> SmbDeleteRun:
    run = db.get(SmbDeleteRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.get("/{cluster_id}", response_model=SmbDeleteInfo)
def get_info(
    cluster_id: str, server: str, share: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage),
) -> SmbDeleteInfo:
    return load_info(db, cluster_id, server, share)


@router.post("", response_model=SmbDeleteRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_delete(
    payload: SmbDeleteRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(_require_hyperv_manage),
) -> SmbDeleteRun:
    if payload.confirm_name != payload.share:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zur Bestätigung den Freigabenamen exakt eintippen.")
    info = load_info(db, payload.cluster_id, payload.server, payload.share)
    if info.blocked_reasons:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(info.blocked_reasons))
    run = SmbDeleteRun(
        hyperv_cluster_id=payload.cluster_id, server=payload.server, share=payload.share,
        netapp_cluster_id=info.netapp_cluster_id, svm_name=info.svm_name, volume_uuid=info.volume_uuid,
        volume_name=info.volume_name, delete_volume=payload.delete_volume and info.volume_deletable,
        capacity_bytes=info.volume_size_bytes, removed_from_groups=[], requested_by=user.display_name or user.username,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_delete, run.id)
    return run


def _execute_delete(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    remaining = ["Freigabe", "Volume"]
    try:
        run = db.get(SmbDeleteRun, run_id)
        if run is None:
            return
        if not run.delete_volume:
            remaining.remove("Volume")
        unc = f"\\\\{run.server}\\{run.share}"
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=SmbDeleteRunStep) as ctx:
                info = load_info(db, run.hyperv_cluster_id, run.server, run.share, own_run_id=run.id)
                if info.blocked_reasons:
                    raise RuntimeError(" ".join(info.blocked_reasons))
                if info.volume_uuid != run.volume_uuid:
                    raise RuntimeError("Das Volume der Freigabe hat sich seit dem Öffnen des Dialogs geändert")
                if run.delete_volume and not info.volume_deletable:
                    raise RuntimeError("Auf dem Volume liegen inzwischen weitere Freigaben/LUNs -- bitte neu öffnen")
                netapp_cluster = db.get(NetAppCluster, run.netapp_cluster_id)
                netapp = _netapp_service_for(netapp_cluster)
                ctx.row.message = f"{unc}, Volume {info.volume_name} ({_gb(info.volume_size_bytes)}), {len(info.other_files)} andere Datei(en)"

            with _StepCtx(db, run.id, "share", "Freigabe löschen", step_model=SmbDeleteRunStep) as ctx:
                netapp.delete_cifs_share(run.svm_name, run.share)
                remaining.remove("Freigabe")
                ctx.row.message = unc

            with _StepCtx(db, run.id, "protection-groups", "Aus Protection Groups austragen", step_model=SmbDeleteRunStep) as ctx:
                names = []
                for group in _groups_with_share(db, run.hyperv_cluster_id, run.server, run.share):
                    group.members = [m for m in (group.members or []) if not _is_share_member(m, run.hyperv_cluster_id, run.server, run.share)]
                    names.append(group.name)
                run.removed_from_groups = names
                ctx.row.message = ", ".join(names) if names else "in keiner Protection Group"

            if run.delete_volume:
                with _StepCtx(db, run.id, "volume", "Volume löschen", step_model=SmbDeleteRunStep) as ctx:
                    netapp.delete_volume_forced(run.volume_uuid)
                    remaining.remove("Volume")
                    now = datetime.now(timezone.utc)
                    marked = 0
                    for row in db.query(BackupRunSnapshot).filter(
                        BackupRunSnapshot.volume_uuid == run.volume_uuid, BackupRunSnapshot.success.is_(True),
                    ):
                        row.success = False
                        row.error_message = f"Volume mit der Freigabe gelöscht ({run.requested_by}, {now:%d.%m.%Y %H:%M} UTC)"
                        marked += 1
                    ctx.row.message = f"{run.volume_name}; {marked} Backup-Eintrag/-Einträge als nicht mehr vorhanden markiert"

            with _StepCtx(db, run.id, "inventory", "Inventory aktualisieren", step_model=SmbDeleteRunStep) as ctx:
                note = ""
                try:
                    _discover_and_persist(db, netapp_cluster)
                except Exception as exc:  # noqa: BLE001
                    note = f"; NetApp-Discovery fehlgeschlagen: {exc}"
                db.query(HyperVSmbShare).filter(
                    HyperVSmbShare.cluster_id == run.hyperv_cluster_id, HyperVSmbShare.server == run.server,
                    HyperVSmbShare.share == run.share,
                ).delete()
                db.commit()
                vhds = db.query(HyperVVhd).filter(HyperVVhd.cluster_id == run.hyperv_cluster_id).all()
                _refresh_smb_share_rows(db, run.hyperv_cluster_id, vhds)
                ctx.row.message = "Freigabe aus dem Inventory entfernt" + note

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"SMB3-Freigabe {unc} gelöscht"
                + (f", Volume {run.volume_name} gelöscht" if run.delete_volume else f" (Volume {run.volume_name} bleibt)")
                + (f", ausgetragen aus {', '.join(run.removed_from_groups)}" if run.removed_from_groups else "")
                + f" (durch {run.requested_by})",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(SmbDeleteRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            # Solange die Freigabe selbst noch steht, wurde nichts geloescht.
            left = f" -- noch vorhanden: {', '.join(remaining)}" if remaining and remaining[0] != "Freigabe" else ""
            run.error_message = f"{detail}{left}"[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"SMB3-Freigabe {run.server}\\{run.share} löschen fehlgeschlagen: {run.error_message} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()
