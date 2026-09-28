"""CSV vergroessern (Backlog #69, Nutzer-Vorgabe 2026-09-28): Aktion in
Inventory > CSVs. Der Dialog zeigt Aggregat, Volume und LUN/CSV live und
grafisch; Volume und LUN werden im Dialog manuell vergroessert (kein
automatisches Mitwachsen des Volumes). Ausfuehrung als Hintergrund-Task:

  Vorpruefung -> Volume vergroessern (optional) -> LUN vergroessern
  (optional) -> Datentraeger auf allen Knoten neu einlesen -> Partition auf
  dem Owner-Knoten erweitern -> pruefen + Inventory aktualisieren

Nur Vergroessern, nie Verkleinern. Scheitert das Erweitern der Partition
nach vergroesserter LUN, wird nichts zurueckgedreht -- der Dialog erkennt
beim naechsten Oeffnen unpartitionierten Platz und bietet "nur Partition
erweitern" an (beide Groessen unveraendert lassen)."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_user_permissions
from app.api.routes.hyperv_clusters import _refresh_csv_rows
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.netapp_clusters import require_storage_unlocked
from app.api.routes.restore import _StepCtx
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import SiteResolver, normalize_node_name
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.csv_resize_run import CsvResizeRun, CsvResizeRunStep
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv
from app.models.netapp_cluster import NetAppCluster, NetAppSystemType
from app.models.netapp_discovery import NetAppLun, NetAppVolume
from app.models.restore_run import RestoreStatus
from app.models.system_log import SystemLogEvent
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/csv-resize", tags=["csv-resize"])

# Unter dieser Differenz gilt eine Partition als "voll ausgedehnt"
# (Ausrichtung/Metadaten am Disk-Ende).
_PARTITION_SLACK_BYTES = 16 * 1024 * 1024


def _require_hyperv_manage(user=Depends(require_storage_unlocked), db: Session = Depends(get_db)):
    """Storage-Recht + offener Storage-Sperrschalter (require_storage_unlocked)
    UND Hyper-V-Recht -- der Vorgang aendert beide Seiten."""
    if Permission.HYPERV_MANAGE not in get_user_permissions(user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Fehlende Berechtigung: hyperv:manage")
    return user


class CsvPart(BaseModel):
    name: str
    path: str | None = None
    owner_node: str | None = None
    state: str | None = None
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    serial_number: str


class PartitionPart(BaseModel):
    disk_size_bytes: int
    partition_size_bytes: int
    partition_max_bytes: int


class LunPart(BaseModel):
    uuid: str
    name: str
    svm_name: str | None = None
    size_bytes: int
    used_bytes: int | None = None
    space_reserved: bool


class VolumePart(BaseModel):
    uuid: str
    name: str
    size_bytes: int
    used_bytes: int | None = None
    available_bytes: int | None = None
    max_size_bytes: int | None = None
    snapshot_reserve_bytes: int | None = None
    snapshot_reserve_percent: int | None = None
    snapshot_used_bytes: int | None = None
    guarantee: str | None = None
    autosize_mode: str | None = None
    # Summe aller LUN-Groessen im Volume (inkl. dieser LUN) und davon die
    # anderen LUNs -- fuer "passt die LUN noch in das Volume".
    luns_total_bytes: int
    other_luns_bytes: int
    lun_count: int


class AggregatePart(BaseModel):
    name: str
    size_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None


class CsvResizeInfo(BaseModel):
    cluster_id: str
    netapp_cluster_id: str
    netapp_cluster_name: str
    csv: CsvPart
    partition: PartitionPart
    lun: LunPart
    volume: VolumePart
    # None = nicht sichtbar (System als einzelne SVM registriert)
    aggregate: AggregatePart | None = None
    aggregate_count: int = 1
    blocked_reason: str | None = None


class CsvResizeRequest(BaseModel):
    cluster_id: str
    csv_name: str = Field(min_length=1)
    new_volume_size_bytes: int | None = None
    new_lun_size_bytes: int | None = None


class CsvResizeRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class CsvResizeRunRead(BaseModel):
    id: str
    csv_name: str
    new_volume_size_bytes: int | None = None
    new_lun_size_bytes: int | None = None
    csv_size_before_bytes: int | None = None
    csv_size_after_bytes: int | None = None
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[CsvResizeRunStepRead]

    class Config:
        from_attributes = True


# --- Live-Ist-Stand --------------------------------------------------------------


def _hyperv_service(cluster: HyperVCluster, address: str | None = None, hostname: str | None = None) -> HyperVService:
    return HyperVService(
        get_settings(), address or cluster.management_address, use_https=cluster.use_https,
        node_hostname=hostname or cluster.hyperv_cluster_name,
    )


def _blocked_reason(db: Session, cluster_id: str, csv_name: str, state: str | None, own_run_id: str | None = None) -> str | None:
    if state and state.lower() != "online":
        return f"Die CSV ist nicht online (Status: {state})."
    running = (
        db.query(CsvResizeRun)
        .filter(
            CsvResizeRun.hyperv_cluster_id == cluster_id, CsvResizeRun.csv_name == csv_name,
            CsvResizeRun.status == RestoreStatus.RUNNING, CsvResizeRun.id != (own_run_id or ""),
        )
        .first()
    )
    if running:
        return "Für diese CSV läuft bereits eine Vergrößerung."
    for run in db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CLEANING_UP])).all():
        if csv_name in (run.targets or []):
            return f"Backup-Lauf '{run.policy_name}' sichert diese CSV gerade -- bitte das Ende abwarten."
    return None


def load_info(db: Session, cluster_id: str, csv_name: str, own_run_id: str | None = None) -> CsvResizeInfo:
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

    password = decrypt_secret(cluster.encrypted_password)
    try:
        cno = _hyperv_service(cluster)
        cno_session = cno.connect(cluster.username, password, read_timeout_sec=30, operation_timeout_sec=20)
        live = next((c for c in cno.list_csvs(cno_session) if c.name == csv_name), None)
        if live is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CSV ist im Cluster nicht (mehr) vorhanden")
        node_address = cno.resolve_node_address(cno_session, live.owner_node)
        node = _hyperv_service(cluster, node_address, live.owner_node)
        partition = node.csv_partition_info(node.connect(cluster.username, password), serial)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc

    netapp = _netapp_service_for(netapp_cluster)
    try:
        lun = netapp.lun_space_by_serial(serial)
        if lun is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LUN auf dem NetApp-System nicht gefunden")
        volume = netapp.volume_space(lun["volume_uuid"])
        aggregate = None
        if netapp_cluster.system_type == NetAppSystemType.CLUSTER and volume["aggregate_names"]:
            aggregate = netapp.aggregate_space(volume["aggregate_names"][0])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NetApp-Abfrage fehlgeschlagen: {exc}") from exc

    luns_total = sum(l["size_bytes"] for l in volume["luns"])
    return CsvResizeInfo(
        cluster_id=cluster_id, netapp_cluster_id=netapp_cluster.id, netapp_cluster_name=netapp_cluster.name,
        csv=CsvPart(
            name=csv_name, path=live.volume_path, owner_node=live.owner_node, state=live.state,
            capacity_bytes=live.capacity_bytes, used_bytes=live.used_bytes, serial_number=serial,
        ),
        partition=PartitionPart(**{k: partition[k] for k in ("disk_size_bytes", "partition_size_bytes", "partition_max_bytes")}),
        lun=LunPart(
            uuid=lun["uuid"], name=lun["name"], svm_name=lun["svm_name"], size_bytes=lun["size_bytes"] or 0,
            used_bytes=lun["used_bytes"], space_reserved=lun["space_reserved"],
        ),
        volume=VolumePart(
            uuid=volume["uuid"], name=volume["name"], size_bytes=volume["size_bytes"] or 0,
            used_bytes=volume["used_bytes"], available_bytes=volume["available_bytes"], max_size_bytes=volume["max_size_bytes"],
            snapshot_reserve_bytes=volume["snapshot_reserve_bytes"], snapshot_reserve_percent=volume["snapshot_reserve_percent"],
            snapshot_used_bytes=volume["snapshot_used_bytes"], guarantee=volume["guarantee"], autosize_mode=volume["autosize_mode"],
            luns_total_bytes=luns_total, other_luns_bytes=luns_total - (lun["size_bytes"] or 0), lun_count=len(volume["luns"]),
        ),
        aggregate=AggregatePart(**aggregate) if aggregate else None,
        aggregate_count=len(volume["aggregate_names"]) or 1,
        blocked_reason=_blocked_reason(db, cluster_id, csv_name, live.state, own_run_id),
    )


def validation_errors(info: CsvResizeInfo, new_volume: int | None, new_lun: int | None) -> list[str]:
    """Harte Grenzen -- identisch im Dialog (dort zusaetzlich weiche
    Warnungen). Leere Liste = zulaessig."""
    errors = []
    if new_volume is not None and new_volume < info.volume.size_bytes:
        errors.append("Das Volume kann nicht verkleinert werden.")
    if new_lun is not None and new_lun < info.lun.size_bytes:
        errors.append("Die LUN kann nicht verkleinert werden.")
    volume_after = new_volume or info.volume.size_bytes
    lun_after = new_lun or info.lun.size_bytes
    if new_volume and info.volume.max_size_bytes and new_volume > info.volume.max_size_bytes:
        errors.append("Die neue Volume-Größe überschreitet die maximale Volume-Größe dieses Systems.")
    if new_volume and info.aggregate and info.aggregate.available_bytes is not None and info.aggregate_count == 1:
        if new_volume - info.volume.size_bytes > info.aggregate.available_bytes:
            errors.append("Im Aggregat ist nicht genug freier Platz für die Volume-Vergrößerung.")
    if info.lun.space_reserved:
        reserve = info.volume.snapshot_reserve_bytes or 0
        if new_volume and info.volume.snapshot_reserve_percent is not None:
            reserve = volume_after * info.volume.snapshot_reserve_percent // 100
        if info.volume.other_luns_bytes + lun_after > volume_after - reserve:
            errors.append(
                "Die LUN ist platzreserviert (thick) und passt in dieser Größe nicht in den Datenbereich des Volumes -- "
                "Volume weiter vergrößern."
            )
    grows = (new_volume or 0) > info.volume.size_bytes or (new_lun or 0) > info.lun.size_bytes
    extendable = info.partition.partition_max_bytes - info.partition.partition_size_bytes > _PARTITION_SLACK_BYTES
    if not grows and not extendable:
        errors.append("Nichts zu tun: weder Volume noch LUN werden größer, und die Partition ist bereits voll ausgedehnt.")
    return errors


@router.get("/runs/{run_id}", response_model=CsvResizeRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> CsvResizeRun:
    run = db.get(CsvResizeRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.get("/{cluster_id}/{csv_name}", response_model=CsvResizeInfo)
def get_info(cluster_id: str, csv_name: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> CsvResizeInfo:
    return load_info(db, cluster_id, csv_name)


@router.post("", response_model=CsvResizeRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_resize(
    payload: CsvResizeRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(_require_hyperv_manage),
) -> CsvResizeRun:
    info = load_info(db, payload.cluster_id, payload.csv_name)
    if info.blocked_reason:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=info.blocked_reason)
    new_volume = payload.new_volume_size_bytes if payload.new_volume_size_bytes and payload.new_volume_size_bytes != info.volume.size_bytes else None
    new_lun = payload.new_lun_size_bytes if payload.new_lun_size_bytes and payload.new_lun_size_bytes != info.lun.size_bytes else None
    errors = validation_errors(info, new_volume, new_lun)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))
    run = CsvResizeRun(
        hyperv_cluster_id=payload.cluster_id, csv_name=payload.csv_name, disk_serial_number=info.csv.serial_number,
        netapp_cluster_id=info.netapp_cluster_id, new_volume_size_bytes=new_volume, new_lun_size_bytes=new_lun,
        csv_size_before_bytes=info.csv.capacity_bytes, requested_by=user.display_name or user.username,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_resize, run.id, info.volume.uuid, info.lun.uuid)
    return run


# --- Ausfuehrung -------------------------------------------------------------------


def _gb(value: int | None) -> str:
    return f"{(value or 0) / 1024**3:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="storage", message=message))
    db.commit()


def _execute_resize(run_id: str, volume_uuid: str, lun_uuid: str) -> None:  # noqa: C901
    db = SessionLocal()
    try:
        run = db.get(CsvResizeRun, run_id)
        if run is None:
            return
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=CsvResizeRunStep) as ctx:
                info = load_info(db, run.hyperv_cluster_id, run.csv_name, own_run_id=run.id)
                if info.blocked_reason:
                    raise RuntimeError(info.blocked_reason)
                errors = validation_errors(info, run.new_volume_size_bytes, run.new_lun_size_bytes)
                if errors:
                    raise RuntimeError(" ".join(errors))
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                netapp = _netapp_service_for(db.get(NetAppCluster, run.netapp_cluster_id))
                ctx.row.message = (
                    f"CSV {_gb(info.csv.capacity_bytes)}, LUN {_gb(info.lun.size_bytes)}, Volume {_gb(info.volume.size_bytes)}, "
                    f"Owner {info.csv.owner_node}"
                )

            if run.new_volume_size_bytes:
                with _StepCtx(db, run.id, "volume", "Volume vergrößern", step_model=CsvResizeRunStep) as ctx:
                    netapp.update_volume(volume_uuid, size_bytes=run.new_volume_size_bytes)
                    ctx.row.message = f"{info.volume.name}: {_gb(info.volume.size_bytes)} → {_gb(run.new_volume_size_bytes)}"
                    volume_row = db.query(NetAppVolume).filter(NetAppVolume.uuid == volume_uuid).first()
                    if volume_row is not None:
                        volume_row.size_bytes = run.new_volume_size_bytes

            if run.new_lun_size_bytes:
                with _StepCtx(db, run.id, "lun", "LUN vergrößern", step_model=CsvResizeRunStep) as ctx:
                    netapp.update_lun(lun_uuid, size_bytes=run.new_lun_size_bytes)
                    ctx.row.message = f"{info.lun.name}: {_gb(info.lun.size_bytes)} → {_gb(run.new_lun_size_bytes)}"
                    lun_row = db.query(NetAppLun).filter(NetAppLun.uuid == lun_uuid).first()
                    if lun_row is not None:
                        lun_row.size_bytes = run.new_lun_size_bytes

            password = decrypt_secret(cluster.encrypted_password)
            cno = _hyperv_service(cluster)
            cno_session = cno.connect(cluster.username, password, read_timeout_sec=30, operation_timeout_sec=20)

            with _StepCtx(db, run.id, "rescan", "Datenträger auf allen Knoten neu einlesen", step_model=CsvResizeRunStep) as ctx:
                node_ips = cno.node_address_map(cno_session)
                nodes = [n.name for n in cno.list_cluster_nodes(cno_session) if n.state == "Up"]
                owner_key = normalize_node_name(info.csv.owner_node)
                failed = []
                for name in nodes:
                    try:
                        node = _hyperv_service(cluster, node_ips.get(name.lower(), name), name)
                        node.rescan_storage(node.connect(cluster.username, password))
                    except Exception as exc:  # noqa: BLE001
                        if normalize_node_name(name) == owner_key:
                            raise RuntimeError(f"Owner-Knoten {name}: {exc}") from exc
                        failed.append(f"{name} ({str(exc)[:120]})")
                ctx.row.message = f"{len(nodes) - len(failed)} von {len(nodes)} Knoten" + (
                    f"; nicht erreicht: {', '.join(failed)}" if failed else ""
                )

            with _StepCtx(db, run.id, "partition", f"Partition auf {info.csv.owner_node} erweitern", step_model=CsvResizeRunStep) as ctx:
                owner = _hyperv_service(cluster, cno.resolve_node_address(cno_session, info.csv.owner_node), info.csv.owner_node)
                owner_session = owner.connect(cluster.username, password, read_timeout_sec=120, operation_timeout_sec=90)
                # Die Disk-Groesse kann nach einem LUN-Resize kurz verzoegert
                # sichtbar werden -- ein paar Versuche, bevor erweitert wird.
                expected = run.new_lun_size_bytes
                for _ in range(6):
                    part = owner.csv_partition_info(owner_session, run.disk_serial_number)
                    if not expected or part["disk_size_bytes"] >= expected - _PARTITION_SLACK_BYTES:
                        break
                    time.sleep(5)
                    owner.rescan_storage(owner_session)
                before, after = owner.extend_csv_partition(owner_session, run.disk_serial_number)
                if expected and after < expected - 2 * _PARTITION_SLACK_BYTES - 128 * 1024 * 1024:
                    raise RuntimeError(
                        f"Partition nur auf {_gb(after)} erweitert, erwartet ~{_gb(expected)} -- Windows sieht die neue "
                        "LUN-Größe noch nicht. Später erneut 'CSV vergrößern' öffnen, dort 'nur Partition erweitern'."
                    )
                ctx.row.message = f"{_gb(before)} → {_gb(after)}"

            with _StepCtx(db, run.id, "verify", "Prüfen und Inventory aktualisieren", step_model=CsvResizeRunStep) as ctx:
                csvs = cno.list_csvs(cno_session)
                live = next((c for c in csvs if c.name == run.csv_name), None)
                _refresh_csv_rows(db, run.hyperv_cluster_id, csvs)
                run.csv_size_after_bytes = live.capacity_bytes if live else None
                ctx.row.message = f"CSV jetzt {_gb(run.csv_size_after_bytes)} (vorher {_gb(run.csv_size_before_bytes)})"

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"CSV '{run.csv_name}' vergrößert: {_gb(run.csv_size_before_bytes)} → {_gb(run.csv_size_after_bytes)}"
                + (f", LUN auf {_gb(run.new_lun_size_bytes)}" if run.new_lun_size_bytes else "")
                + (f", Volume auf {_gb(run.new_volume_size_bytes)}" if run.new_volume_size_bytes else "")
                + f" (durch {run.requested_by})",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(CsvResizeRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            run.error_message = str(detail)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"CSV '{run.csv_name}' vergrößern fehlgeschlagen: {detail} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()
