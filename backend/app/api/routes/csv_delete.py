"""CSV loeschen (Nutzer-Vorgaben 2026-09-30): Aktion in Inventory > CSVs.
Gegenstueck zu app.api.routes.csv_create. Ablauf als Hintergrund-Task:

  Vorpruefung -> CSV entfernen -> Cluster-Disk entfernen -> aus Protection
  Groups austragen -> LUN-Mapping aufheben -> LUN loeschen -> Volume loeschen
  (nur wenn einzige LUN und gewaehlt) -> Datentraeger neu einlesen +
  Inventory aktualisieren

LUN und Volume sind Opt-out: ohne "LUN loeschen" bleiben LUN und Mapping
unangetastet (die Disk kann spaeter wieder in den Cluster aufgenommen werden),
das Volume laesst sich nur zusammen mit der LUN loeschen.

Regeln (vom Nutzer bestaetigt):
- Gesperrt, solange eine VM/VHDX auf der CSV liegt (Inventory UND live
  gescannte VM-Dateien); andere Dateien nur als Warnung mit Liste.
- Gesperrt, solange das Volume Quelle einer SnapMirror-Beziehung ist (erst in
  Storage aufloesen). SnapMirror-Ziele bleiben unangetastet.
- Mit dem Volume verschwinden die Backup-Snapshots dieser CSV: der Dialog
  zeigt Anzahl und Zeitraum, die Backup-Eintraege werden danach als nicht
  mehr vorhanden markiert (success=False), wie beim manuellen Snapshot-
  Loeschen. Eintraege von SnapMirror-Kopien bleiben gueltig.
- Automatisch aus allen Protection Groups entfernt.
- Volume nur, wenn die LUN die einzige darin ist.
- Bestaetigung durch Eintippen des CSV-Namens (auch serverseitig geprueft).

Kein Zurueckrollen: bricht ein Schritt ab, nennt die Fehlermeldung die noch
vorhandenen Objekte (Aufraeumen dann in Storage > LUNs/Volumes)."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.csv_create import _Cluster
from app.api.routes.csv_resize import _require_hyperv_manage
from app.api.routes.hyperv_clusters import _refresh_csv_rows
from app.api.routes.netapp_clusters import _discover_and_persist
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.restore import _StepCtx
from app.core.crypto import decrypt_secret
from app.core.sites import SiteResolver
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, BackupRunSnapshot, JobStatus
from app.models.csv_create_run import CsvCreateRun
from app.models.csv_delete_run import CsvDeleteRun, CsvDeleteRunStep
from app.models.csv_resize_run import CsvResizeRun
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSnapMirrorRelationship
from app.models.resource_group import ResourceGroup, parse_member_key
from app.models.restore_run import RestoreStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_move_run import VmMoveRun

router = APIRouter(prefix="/api/csv-delete", tags=["csv-delete"])

GIB = 1024**3


class FileEntry(BaseModel):
    path: str
    size_bytes: int


class SnapshotSummary(BaseModel):
    total: int
    backup_count: int
    oldest_backup: str | None = None
    newest_backup: str | None = None


class CsvDeleteInfo(BaseModel):
    cluster_id: str
    csv_name: str
    csv_path: str | None = None
    owner_node: str | None = None
    state: str | None = None
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    serial_number: str
    netapp_cluster_id: str
    netapp_cluster_name: str
    lun_uuid: str
    lun_name: str
    svm_name: str | None = None
    lun_size_bytes: int
    igroups: list[str]
    volume_uuid: str
    volume_name: str
    volume_size_bytes: int
    other_luns: list[str]
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


class CsvDeleteRequest(BaseModel):
    cluster_id: str
    csv_name: str
    confirm_name: str
    delete_lun: bool = True
    delete_volume: bool = True


class CsvDeleteRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class CsvDeleteRunRead(BaseModel):
    id: str
    csv_name: str
    lun_name: str
    volume_name: str
    delete_lun: bool
    delete_volume: bool
    capacity_bytes: int | None = None
    removed_from_groups: list[str] = []
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[CsvDeleteRunStepRead]

    class Config:
        from_attributes = True


def _gb(value: int | None) -> str:
    return f"{(value or 0) / GIB:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="storage", message=message))
    db.commit()


def _groups_with_csv(db: Session, cluster_id: str, csv_name: str) -> list[ResourceGroup]:
    result = []
    for group in db.query(ResourceGroup).all():
        for member in group.members or []:
            member_cluster, name = parse_member_key(member)
            if name == csv_name and member_cluster in (cluster_id, None):
                result.append(group)
                break
    return result


def _busy_reasons(db: Session, cluster_id: str, csv_name: str, own_run_id: str | None = None) -> list[str]:
    reasons = []
    for run in db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CLEANING_UP])).all():
        if csv_name in (run.targets or []):
            reasons.append(f"Backup-Lauf '{run.policy_name}' sichert diese CSV gerade.")
    if db.query(CsvResizeRun).filter(
        CsvResizeRun.hyperv_cluster_id == cluster_id, CsvResizeRun.csv_name == csv_name, CsvResizeRun.status == RestoreStatus.RUNNING,
    ).first():
        reasons.append("Für diese CSV läuft gerade eine Vergrößerung.")
    if db.query(VmMoveRun).filter(
        VmMoveRun.hyperv_cluster_id == cluster_id, VmMoveRun.destination_csv_name == csv_name, VmMoveRun.status == RestoreStatus.RUNNING,
    ).first():
        reasons.append("Gerade wird eine VM auf diese CSV verschoben.")
    if db.query(CsvCreateRun).filter(
        CsvCreateRun.hyperv_cluster_id == cluster_id, CsvCreateRun.status == RestoreStatus.RUNNING,
    ).first():
        reasons.append("Auf diesem Cluster wird gerade eine CSV angelegt.")
    if db.query(CsvDeleteRun).filter(
        CsvDeleteRun.hyperv_cluster_id == cluster_id, CsvDeleteRun.csv_name == csv_name,
        CsvDeleteRun.status == RestoreStatus.RUNNING, CsvDeleteRun.id != (own_run_id or ""),
    ).first():
        reasons.append("Diese CSV wird bereits gelöscht.")
    return reasons


def load_info(db: Session, cluster_id: str, csv_name: str, own_run_id: str | None = None) -> CsvDeleteInfo:  # noqa: C901
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    csv_row = db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id, HyperVCsv.name == csv_name).first()
    if csv_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CSV nicht gefunden")
    serial = csv_row.disk_serial_number
    if not serial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Für diese CSV ist keine Disk-Seriennummer bekannt -- bitte zuerst eine Discovery ausführen.",
        )
    netapp_cluster_id = SiteResolver(db).netapp_cluster_id_for_serial(serial)
    netapp_cluster = db.get(NetAppCluster, netapp_cluster_id) if netapp_cluster_id else None
    if netapp_cluster is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Die zugehörige NetApp-LUN ist nicht bekannt -- NetApp-System registriert und discovert?",
        )

    blocked: list[str] = _busy_reasons(db, cluster_id, csv_name, own_run_id)
    warnings: list[str] = []

    # --- Hyper-V (live) ---
    password = decrypt_secret(cluster.encrypted_password)
    try:
        cno = _Cluster(cluster, password)
        live = next((c for c in cno.service.list_csvs(cno.session) if c.name == csv_name), None)
        if live is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CSV ist im Cluster nicht (mehr) vorhanden")
        if (live.disk_serial_number or "") != serial:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Die Disk-Seriennummer der CSV weicht vom Inventory ab -- bitte zuerst eine Discovery ausführen.",
            )
        scan = None
        if (live.state or "").lower() == "online" and live.volume_path:
            scan = cno.service.scan_csv_files(cno.session, live.volume_path)
        else:
            blocked.append(f"Die CSV ist nicht online (Status: {live.state}) -- ihr Inhalt lässt sich nicht prüfen.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc

    vms = sorted({
        r.vm_name for r in db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id, HyperVVhd.csv_name == csv_name)
        if r.vm_name
    })
    vm_files = scan["vm_files"] if scan else []
    other_files = [f for f in (scan["files"] if scan else []) if f not in vm_files]
    if vms:
        blocked.append(f"Auf der CSV liegen laut Inventory noch VMs: {', '.join(vms)}.")
    if vm_files:
        blocked.append(f"Auf der CSV liegen noch {len(vm_files)} VM-Datei(en) (VHDX/Konfiguration).")
    if other_files:
        warnings.append(
            f"Auf der CSV liegen noch {len(other_files)}{'+' if scan and scan['truncated'] else ''} andere Datei(en) -- "
            "sie gehen verloren, wenn die LUN gelöscht wird."
        )

    # --- NetApp (live) ---
    netapp = _netapp_service_for(netapp_cluster)
    try:
        lun = netapp.lun_space_by_serial(serial)
        if lun is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LUN auf dem NetApp-System nicht gefunden")
        volume = netapp.volume_space(lun["volume_uuid"])
        igroups = netapp.lun_igroup_names(lun["uuid"])
        snapshots = netapp.list_snapshots(lun["volume_uuid"])
        destinations = netapp.snapmirror_destinations_of(lun["svm_name"], volume["name"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NetApp-Abfrage fehlgeschlagen: {exc}") from exc
    # Zusaetzlich die discoverten Beziehungen (Ziel-System registriert, aber
    # list_destinations_only liefert auf manchen Versionen nichts).
    source_path = f"{lun['svm_name']}:{volume['name']}"
    for rel in db.query(NetAppSnapMirrorRelationship).filter(NetAppSnapMirrorRelationship.source_path == source_path):
        if rel.destination_path and rel.destination_path not in destinations:
            destinations.append(rel.destination_path)

    other_luns = [l["name"] for l in volume["luns"] if l["name"] != lun["name"]]
    volume_deletable = not other_luns
    if destinations:
        blocked.append(
            f"Das Volume {volume['name']} ist Quelle einer SnapMirror-Beziehung ({', '.join(destinations)}) -- "
            "bitte die Beziehung zuerst in Storage auflösen. Die Ziel-Volumes bleiben dabei erhalten."
        )

    backup_names = {
        r.snapshot_name for r in db.query(BackupRunSnapshot).filter(
            BackupRunSnapshot.volume_uuid == lun["volume_uuid"], BackupRunSnapshot.success.is_(True),
        ) if r.snapshot_name
    }
    backup_times = sorted(s["create_time"] for s in snapshots if s["name"] in backup_names and s["create_time"])
    summary = SnapshotSummary(
        total=len(snapshots), backup_count=len([s for s in snapshots if s["name"] in backup_names]),
        oldest_backup=backup_times[0] if backup_times else None, newest_backup=backup_times[-1] if backup_times else None,
    )
    if volume_deletable and summary.total:
        warnings.append(
            f"Wird das Volume mit gelöscht, verschwinden {summary.total} Snapshot(s), davon {summary.backup_count} Backup(s) dieser App "
            "-- diese Backups sind danach nicht mehr wiederherstellbar (Kopien auf SnapMirror-Zielen bleiben)."
        )
    if other_luns:
        warnings.append(
            f"Im Volume {volume['name']} liegen noch weitere LUNs ({', '.join(other_luns)}) -- das Volume bleibt stehen, "
            "gelöscht werden nur CSV und LUN."
        )
    groups = [g.name for g in _groups_with_csv(db, cluster_id, csv_name)]
    if groups:
        warnings.append(f"Die CSV wird aus den Protection Groups {', '.join(groups)} ausgetragen.")

    return CsvDeleteInfo(
        cluster_id=cluster_id, csv_name=csv_name, csv_path=live.volume_path, owner_node=live.owner_node, state=live.state,
        capacity_bytes=live.capacity_bytes, used_bytes=live.used_bytes, serial_number=serial,
        netapp_cluster_id=netapp_cluster.id, netapp_cluster_name=netapp_cluster.name,
        lun_uuid=lun["uuid"], lun_name=lun["name"], svm_name=lun["svm_name"], lun_size_bytes=lun["size_bytes"] or 0,
        igroups=igroups, volume_uuid=volume["uuid"], volume_name=volume["name"], volume_size_bytes=volume["size_bytes"] or 0,
        other_luns=other_luns, volume_deletable=volume_deletable, snapshots=summary, snapmirror_destinations=destinations,
        vms=vms, vm_files=[FileEntry(**f) for f in vm_files], other_files=[FileEntry(**f) for f in other_files],
        files_truncated=bool(scan and scan["truncated"]), protection_groups=groups, blocked_reasons=blocked, warnings=warnings,
    )


@router.get("/runs/{run_id}", response_model=CsvDeleteRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> CsvDeleteRun:
    run = db.get(CsvDeleteRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.get("/{cluster_id}/{csv_name}", response_model=CsvDeleteInfo)
def get_info(cluster_id: str, csv_name: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> CsvDeleteInfo:
    return load_info(db, cluster_id, csv_name)


@router.post("", response_model=CsvDeleteRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_delete(
    payload: CsvDeleteRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(_require_hyperv_manage),
) -> CsvDeleteRun:
    if payload.confirm_name != payload.csv_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zur Bestätigung den CSV-Namen exakt eintippen.")
    info = load_info(db, payload.cluster_id, payload.csv_name)
    if info.blocked_reasons:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(info.blocked_reasons))
    run = CsvDeleteRun(
        hyperv_cluster_id=payload.cluster_id, csv_name=payload.csv_name, disk_serial_number=info.serial_number,
        netapp_cluster_id=info.netapp_cluster_id, lun_uuid=info.lun_uuid, lun_name=info.lun_name, svm_name=info.svm_name,
        volume_uuid=info.volume_uuid, volume_name=info.volume_name,
        delete_lun=payload.delete_lun,
        delete_volume=payload.delete_lun and payload.delete_volume and info.volume_deletable, capacity_bytes=info.capacity_bytes,
        removed_from_groups=[], requested_by=user.display_name or user.username,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_delete, run.id)
    return run


def _execute_delete(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    # Was nach einem Abbruch noch existiert -- fuer eine klare Fehlermeldung.
    remaining = ["CSV", "Cluster-Disk", "LUN-Mapping", "LUN", "Volume"]
    try:
        run = db.get(CsvDeleteRun, run_id)
        if run is None:
            return
        if not run.delete_volume:
            remaining.remove("Volume")
        if not run.delete_lun:
            remaining.remove("LUN")
            remaining.remove("LUN-Mapping")
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=CsvDeleteRunStep) as ctx:
                info = load_info(db, run.hyperv_cluster_id, run.csv_name, own_run_id=run.id)
                if info.blocked_reasons:
                    raise RuntimeError(" ".join(info.blocked_reasons))
                if info.lun_uuid != run.lun_uuid or info.volume_uuid != run.volume_uuid:
                    raise RuntimeError("LUN oder Volume der CSV haben sich seit dem Öffnen des Dialogs geändert")
                if run.delete_volume and not info.volume_deletable:
                    raise RuntimeError("Im Volume liegen inzwischen weitere LUNs -- Volume wird nicht gelöscht, bitte neu öffnen")
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                netapp_cluster = db.get(NetAppCluster, run.netapp_cluster_id)
                netapp = _netapp_service_for(netapp_cluster)
                cno = _Cluster(cluster, decrypt_secret(cluster.encrypted_password))
                ctx.row.message = (
                    f"{info.csv_path} ({_gb(info.capacity_bytes)}), LUN {info.lun_name}, Volume {info.volume_name}, "
                    f"{len(info.other_files)} andere Datei(en)"
                )

            with _StepCtx(db, run.id, "csv", "CSV entfernen", step_model=CsvDeleteRunStep) as ctx:
                _, note = cno.call(lambda s, sess: s.remove_cluster_shared_volume(sess, run.csv_name))
                remaining.remove("CSV")
                ctx.row.message = f"'{run.csv_name}' ist keine CSV mehr{note}"

            with _StepCtx(db, run.id, "cluster-disk", "Cluster-Disk entfernen", step_model=CsvDeleteRunStep) as ctx:
                _, note = cno.call(lambda s, sess: s.remove_cluster_resource(sess, run.csv_name))
                remaining.remove("Cluster-Disk")
                ctx.row.message = f"Ressource '{run.csv_name}' entfernt{note}"

            with _StepCtx(db, run.id, "protection-groups", "Aus Protection Groups austragen", step_model=CsvDeleteRunStep) as ctx:
                names = []
                for group in _groups_with_csv(db, run.hyperv_cluster_id, run.csv_name):
                    group.members = [
                        m for m in (group.members or [])
                        if not (parse_member_key(m)[1] == run.csv_name and parse_member_key(m)[0] in (run.hyperv_cluster_id, None))
                    ]
                    names.append(group.name)
                run.removed_from_groups = names
                ctx.row.message = ", ".join(names) if names else "in keiner Protection Group"

            if run.delete_lun:
                with _StepCtx(db, run.id, "unmap", "LUN-Mapping aufheben", step_model=CsvDeleteRunStep) as ctx:
                    removed = netapp.unmap_lun_everywhere(run.lun_uuid)
                    remaining.remove("LUN-Mapping")
                    ctx.row.message = ", ".join(removed) if removed else "keine Zuordnung vorhanden"

                with _StepCtx(db, run.id, "lun", "LUN löschen", step_model=CsvDeleteRunStep) as ctx:
                    netapp.delete_lun(run.lun_uuid)
                    remaining.remove("LUN")
                    ctx.row.message = run.lun_name

            if run.delete_volume:
                with _StepCtx(db, run.id, "volume", "Volume löschen", step_model=CsvDeleteRunStep) as ctx:
                    netapp.delete_volume_forced(run.volume_uuid)
                    remaining.remove("Volume")
                    now = datetime.now(timezone.utc)
                    marked = 0
                    for row in db.query(BackupRunSnapshot).filter(
                        BackupRunSnapshot.volume_uuid == run.volume_uuid, BackupRunSnapshot.success.is_(True),
                    ):
                        row.success = False
                        row.error_message = (
                            f"Volume mit der CSV gelöscht (CSV löschen, {run.requested_by}, {now:%d.%m.%Y %H:%M} UTC)"
                        )
                        marked += 1
                    ctx.row.message = f"{run.volume_name}; {marked} Backup-Eintrag/-Einträge als nicht mehr vorhanden markiert"

            with _StepCtx(db, run.id, "inventory", "Datenträger neu einlesen und Inventory aktualisieren", step_model=CsvDeleteRunStep) as ctx:
                node_ips = cno.service.node_address_map(cno.session)
                done, failed = 0, []
                for node in cno.service.list_cluster_nodes(cno.session):
                    if node.state not in ("Up", "Paused"):
                        continue
                    try:
                        service, session = cno.node(node.name, node_ips)
                        service.rescan_storage(session)
                        done += 1
                    except Exception as exc:  # noqa: BLE001
                        failed.append(f"{node.name} ({str(exc)[:80]})")
                time.sleep(2)
                _refresh_csv_rows(db, run.hyperv_cluster_id, cno.service.list_csvs(cno.session))
                note = ""
                try:
                    _discover_and_persist(db, netapp_cluster)
                except Exception as exc:  # noqa: BLE001
                    note = f"; NetApp-Discovery fehlgeschlagen: {exc}"
                ctx.row.message = f"{done} Knoten neu eingelesen" + (f", nicht erreicht: {', '.join(failed)}" if failed else "") + note

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"CSV '{run.csv_name}' gelöscht ({_gb(run.capacity_bytes)})"
                + (f": LUN {run.lun_name}" if run.delete_lun else f"; LUN {run.lun_name} samt Mapping bleibt")
                + (f", Volume {run.volume_name}" if run.delete_volume else f" (Volume {run.volume_name} bleibt)")
                + (f", ausgetragen aus {', '.join(run.removed_from_groups)}" if run.removed_from_groups else "")
                + f" (durch {run.requested_by})",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(CsvDeleteRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            # Solange die CSV selbst noch steht, wurde nichts geloescht.
            left = f" -- noch vorhanden: {', '.join(remaining)}" if remaining and remaining[0] != "CSV" else ""
            run.error_message = f"{detail}{left}"[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"CSV '{run.csv_name}' löschen fehlgeschlagen: {run.error_message} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()
