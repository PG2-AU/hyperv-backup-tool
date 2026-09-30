"""Neue CSV per Assistent anlegen (Backlog #68, Nutzer-Vorgaben 2026-09-30):
Aktion in Inventory > CSVs. Ablauf als Hintergrund-Task mit Schritt-Protokoll:

  Vorpruefung -> Volume anlegen -> LUN anlegen -> auf igroup(s) mappen ->
  Datentraeger auf allen Knoten einlesen -> auf einem Knoten initialisieren
  und formatieren -> als Cluster-Disk aufnehmen -> zur CSV machen ->
  Mount-Ordner umbenennen (optional) -> Inventory aktualisieren ->
  Protection Group (optional)

Standardwerte (vom Nutzer bestaetigt): eine LUN pro Volume; Volume thin,
Snapshot-Policy 'none' (die Snapshots macht die App), Snapshot-Reserve 0 %,
Autosize 'grow' optional; LUN OS-Type hyper_v, thin, Space Allocation an;
gleiche LUN-ID auf allen igroups; GPT + NTFS mit 64 KB (ReFS nur mit Warnung,
laeuft auf SAN-CSVs immer umgeleitet).

Bricht ein Schritt ab, wird NICHTS automatisch entfernt: der Dialog zeigt
die von diesem Lauf angelegten Objekte und fragt nach (Nutzer-Vorgabe) --
"Zurueckrollen" entfernt genau diese in umgekehrter Reihenfolge, "Behalten"
laesst alles stehen. Die Schritte nach der fertigen CSV (Ordner umbenennen,
Inventory, Protection Group) gelten nur als Warnung, damit eine
funktionierende CSV nie zum Zurueckrollen angeboten wird.

Der Standort (#62) wird nicht gesetzt, sondern ergibt sich automatisch aus
dem NetApp-System der LUN (SiteResolver), sobald die LUN discovert ist."""

import copy
import re
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_user_permissions
from app.api.routes.csv_resize import _require_hyperv_manage
from app.api.routes.hyperv_clusters import _refresh_csv_rows
from app.api.routes.netapp_clusters import _discover_and_persist
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.restore import _StepCtx
from app.api.routes.vm_moves import _is_access_denied
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import SiteResolver, _is_mcc_mirror_svm, normalize_node_name
from app.db.session import SessionLocal, get_db
from app.models.backup_policy import BackupScope
from app.models.csv_create_run import CsvCreateRun, CsvCreateRunStep
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSvm
from app.models.resource_group import ResourceGroup, make_member_key
from app.models.restore_run import RestoreStatus, RestoreStepStatus
from app.models.system_log import SystemLogEvent
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/csv-create", tags=["csv-create"])

GIB = 1024**3
_CSV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_VOLUME_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,202}$")
_LUN_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
# Wie lange nach dem Mappen auf jeden Knoten gewartet wird, bis er die LUN sieht.
_DISK_WAIT_SEC = 90


# --- Schemas --------------------------------------------------------------------------


class NodeInitiators(BaseModel):
    name: str
    state: str
    initiators: list[dict] = []
    error: str | None = None


class HyperVOptions(BaseModel):
    cluster_id: str
    nodes: list[NodeInitiators]
    csv_names: list[str]
    busy_reason: str | None = None


class SvmOption(BaseModel):
    name: str
    allowed_protocols: str | None = None


class AggregateOption(BaseModel):
    name: str
    state: str | None = None
    size_bytes: int | None = None
    available_bytes: int | None = None


class IgroupOption(BaseModel):
    name: str
    svm_name: str | None = None
    os_type: str | None = None
    protocol: str | None = None
    initiators: list[str] = []


class NetAppOptions(BaseModel):
    netapp_cluster_id: str
    system_type: str
    svms: list[SvmOption]
    aggregates: list[AggregateOption]
    igroups: list[IgroupOption]


class CsvCreateRequest(BaseModel):
    cluster_id: str
    netapp_cluster_id: str
    svm_name: str = Field(min_length=1)
    aggregate_name: str | None = None
    csv_name: str
    volume_name: str
    lun_name: str
    lun_size_bytes: int = Field(ge=GIB)
    volume_size_bytes: int = Field(ge=GIB)
    autosize_grow: bool = True
    igroup_names: list[str] = Field(min_length=1)
    file_system: Literal["NTFS", "ReFS"] = "NTFS"
    allocation_unit: Literal[4096, 65536] = 65536
    rename_folder: bool = True
    resource_group_id: str | None = None


class CsvCreateRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class CsvCreateRunRead(BaseModel):
    id: str
    csv_name: str
    svm_name: str
    volume_name: str
    lun_name: str
    volume_size_bytes: int
    lun_size_bytes: int
    created_volume_uuid: str | None = None
    created_lun_uuid: str | None = None
    lun_id: int | None = None
    mapped_igroups: list[str] = []
    format_node: str | None = None
    disk_formatted: bool = False
    cluster_resource_name: str | None = None
    csv_added: bool = False
    csv_path: str | None = None
    rollback_declined: bool = False
    has_created_objects: bool = False
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[CsvCreateRunStepRead]

    class Config:
        from_attributes = True


# --- Hilfen ---------------------------------------------------------------------------


def _hyperv_service(cluster: HyperVCluster, address: str | None = None, hostname: str | None = None, settings=None) -> HyperVService:
    return HyperVService(
        settings or get_settings(), address or cluster.management_address, use_https=cluster.use_https,
        node_hostname=hostname or cluster.hyperv_cluster_name,
    )


def _gb(value: int | None) -> str:
    return f"{(value or 0) / GIB:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="storage", message=message))
    db.commit()


def _busy_reason(db: Session, cluster_id: str, own_run_id: str | None = None) -> str | None:
    running = (
        db.query(CsvCreateRun)
        .filter(
            CsvCreateRun.hyperv_cluster_id == cluster_id, CsvCreateRun.status == RestoreStatus.RUNNING,
            CsvCreateRun.id != (own_run_id or ""),
        )
        .first()
    )
    if running:
        return f"Auf diesem Cluster wird gerade die CSV '{running.csv_name}' angelegt bzw. zurückgerollt -- bitte das Ende abwarten."
    return None


def normalize_initiator(address: str) -> str:
    """IQN unveraendert (kleingeschrieben); WWPN ohne ':'/'-' -- ONTAP
    speichert '20:00:00:25:b5:..', Windows liefert '20000025B5..'."""
    value = (address or "").strip().lower()
    return value if value.startswith(("iqn.", "eui.", "naa.")) else value.replace(":", "").replace("-", "")


class _Cluster:
    """CNO-Sitzung mit einmaligem CredSSP-Rueckfall fuer Cluster-API-Aufrufe
    (Add-ClusterDisk & Co.) -- gleiches Double-Hop-Muster wie Add-Cluster-
    VirtualMachineRole/Host-Move, siehe app.api.routes.vm_moves."""

    def __init__(self, cluster: HyperVCluster, password: str):
        self.cluster = cluster
        self.password = password
        self.settings = get_settings()
        self.service = _hyperv_service(cluster)
        self.session = self.service.connect(cluster.username, password, read_timeout_sec=180, operation_timeout_sec=120)
        self._credssp: tuple[HyperVService, object] | None = None

    def call(self, fn) -> tuple[object, str]:
        try:
            return fn(self.service, self.session), ""
        except RuntimeError as exc:
            if not _is_access_denied(exc) or self.settings.winrm_transport == "credssp":
                raise
        if self._credssp is None:
            settings = copy.copy(self.settings)
            settings.winrm_transport = "credssp"
            service = _hyperv_service(self.cluster, settings=settings)
            self._credssp = (service, service.connect(self.cluster.username, self.password, read_timeout_sec=180, operation_timeout_sec=120))
        return fn(*self._credssp), f" (per CredSSP, {self.settings.winrm_transport} wurde abgelehnt)"

    def node(self, name: str, node_ips: dict[str, str]) -> tuple[HyperVService, object]:
        service = _hyperv_service(self.cluster, node_ips.get(name.lower(), name), name)
        return service, service.connect(self.cluster.username, self.password, read_timeout_sec=300, operation_timeout_sec=240)


def _get_cluster(db: Session, cluster_id: str) -> HyperVCluster:
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    return cluster


def _get_netapp(db: Session, netapp_cluster_id: str) -> NetAppCluster:
    netapp = db.get(NetAppCluster, netapp_cluster_id)
    if netapp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetApp-System nicht gefunden")
    return netapp


# --- Auswahl-Optionen (live) ---------------------------------------------------------


@router.get("/hyperv/{cluster_id}", response_model=HyperVOptions)
def hyperv_options(cluster_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> HyperVOptions:
    """Knoten mit Status und Initiatoren (IQN/WWPN) -- fuer den Vorschlag der
    passenden igroups -- und die vorhandenen CSV-Namen."""
    cluster = _get_cluster(db, cluster_id)
    password = decrypt_secret(cluster.encrypted_password)
    try:
        cno = _hyperv_service(cluster)
        cno_session = cno.connect(cluster.username, password, read_timeout_sec=30, operation_timeout_sec=20)
        nodes = cno.list_cluster_nodes(cno_session)
        node_ips = cno.node_address_map(cno_session)
        csv_names = [c.name for c in cno.list_csvs(cno_session)]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc
    result = []
    for node in sorted(nodes, key=lambda n: n.name.lower()):
        entry = NodeInitiators(name=node.name, state=node.state)
        if node.state in ("Up", "Paused"):
            try:
                service = _hyperv_service(cluster, node_ips.get(node.name.lower(), node.name), node.name)
                entry.initiators = service.list_initiator_ports(service.connect(cluster.username, password))
            except Exception as exc:  # noqa: BLE001
                entry.error = str(exc)[:300]
        result.append(entry)
    return HyperVOptions(cluster_id=cluster_id, nodes=result, csv_names=csv_names, busy_reason=_busy_reason(db, cluster_id))


@router.get("/netapp/{netapp_cluster_id}", response_model=NetAppOptions)
def netapp_options(netapp_cluster_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> NetAppOptions:
    netapp = _get_netapp(db, netapp_cluster_id)
    svms = [
        SvmOption(name=s.name, allowed_protocols=s.allowed_protocols)
        for s in db.query(NetAppSvm).filter(NetAppSvm.cluster_id == netapp_cluster_id).order_by(NetAppSvm.name).all()
        if not _is_mcc_mirror_svm(s) and (s.state or "running").lower() == "running"
    ]
    service = _netapp_service_for(netapp)
    try:
        aggregates = service.csv_create_aggregates()
        igroups = service.csv_create_igroups()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NetApp-Abfrage fehlgeschlagen: {exc}") from exc
    return NetAppOptions(
        netapp_cluster_id=netapp_cluster_id, system_type=netapp.system_type.value, svms=svms,
        aggregates=[AggregateOption(**a) for a in aggregates if a.get("name")],
        igroups=[IgroupOption(**i) for i in igroups if i.get("name")],
    )


# --- Start ------------------------------------------------------------------------------


def _validate(db: Session, payload: CsvCreateRequest, user) -> None:
    errors = []
    if not _CSV_NAME_RE.match(payload.csv_name):
        errors.append("CSV-Name: nur Buchstaben, Ziffern, '_', '-', '.' (max. 63 Zeichen, erstes Zeichen Buchstabe/Ziffer).")
    if not _VOLUME_NAME_RE.match(payload.volume_name):
        errors.append("Volume-Name: nur Buchstaben, Ziffern und '_' (erstes Zeichen Buchstabe oder '_').")
    if not _LUN_NAME_RE.match(payload.lun_name):
        errors.append("LUN-Name: nur Buchstaben, Ziffern, '_', '-', '.'.")
    if payload.volume_size_bytes < payload.lun_size_bytes:
        errors.append("Das Volume muss mindestens so groß wie die LUN sein.")
    existing = db.query(HyperVCsv).filter(HyperVCsv.cluster_id == payload.cluster_id).all()
    if any(c.name.lower() == payload.csv_name.lower() for c in existing):
        errors.append(f"Eine CSV '{payload.csv_name}' gibt es auf diesem Cluster bereits.")
    if payload.resource_group_id:
        group = db.get(ResourceGroup, payload.resource_group_id)
        if group is None or group.scope != BackupScope.CSV:
            errors.append("Die gewählte Protection Group gibt es nicht oder sie enthält keine CSVs.")
        elif Permission.BACKUP_CREATE not in get_user_permissions(user, db):
            errors.append("Für die Zuordnung zu einer Protection Group fehlt die Berechtigung backup:create.")
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))


@router.get("/runs/{run_id}", response_model=CsvCreateRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> CsvCreateRun:
    run = db.get(CsvCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.post("", response_model=CsvCreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_create(
    payload: CsvCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(_require_hyperv_manage),
) -> CsvCreateRun:
    _get_cluster(db, payload.cluster_id)
    _get_netapp(db, payload.netapp_cluster_id)
    _validate(db, payload, user)
    busy = _busy_reason(db, payload.cluster_id)
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    run = CsvCreateRun(
        hyperv_cluster_id=payload.cluster_id, netapp_cluster_id=payload.netapp_cluster_id, csv_name=payload.csv_name,
        svm_name=payload.svm_name, volume_name=payload.volume_name, lun_name=payload.lun_name,
        volume_size_bytes=payload.volume_size_bytes, lun_size_bytes=payload.lun_size_bytes,
        options={
            "aggregate_name": payload.aggregate_name, "autosize_grow": payload.autosize_grow,
            "igroup_names": payload.igroup_names, "file_system": payload.file_system,
            "allocation_unit": payload.allocation_unit, "rename_folder": payload.rename_folder,
            "resource_group_id": payload.resource_group_id,
        },
        mapped_igroups=[], requested_by=user.display_name or user.username,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_create, run.id)
    return run


@router.post("/runs/{run_id}/rollback", response_model=CsvCreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_rollback(
    run_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage),
) -> CsvCreateRun:
    run = db.get(CsvCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status != RestoreStatus.FAILED or not run.has_created_objects or run.rollback_declined:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Für diesen Lauf gibt es nichts zurückzurollen.")
    busy = _busy_reason(db, run.hyperv_cluster_id, own_run_id=run.id)
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    run.status = RestoreStatus.RUNNING
    run.finished_at = None
    db.commit()
    _log(db, f"CSV '{run.csv_name}' anlegen: Zurückrollen gestartet (durch {user.display_name or user.username})")
    background_tasks.add_task(_execute_rollback, run.id)
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/keep", response_model=CsvCreateRunRead)
def keep_objects(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> CsvCreateRun:
    run = db.get(CsvCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status == RestoreStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Lauf ist noch aktiv.")
    run.rollback_declined = True
    db.commit()
    _log(db, f"CSV '{run.csv_name}' anlegen: angelegte Objekte nach Fehler bewusst behalten (durch {user.display_name or user.username})")
    return run


# --- Ausfuehrung ----------------------------------------------------------------------


def _soft_step(db: Session, run_id: str, step: str, label: str, fn, warnings: list[str], step_model: type = CsvCreateRunStep) -> None:
    """Schritt nach der fertigen CSV: ein Fehler wird als Warnung
    protokolliert (Schritt rot), bricht den Lauf aber nicht ab. Auch von
    smb_create genutzt (dort mit eigenem step_model)."""
    with _StepCtx(db, run_id, step, label, step_model=step_model) as ctx:
        try:
            ctx.row.message = fn() or None
        except Exception as exc:  # noqa: BLE001
            ctx.row.message = f"Warnung: {exc}"[:2000]
            warnings.append(f"{label}: {exc}")
    if ctx.row.message and ctx.row.message.startswith("Warnung:"):
        ctx.row.status = RestoreStepStatus.ERROR
        db.commit()


def _execute_create(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    try:
        run = db.get(CsvCreateRun, run_id)
        if run is None:
            return
        opts = run.options or {}
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=CsvCreateRunStep) as ctx:
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                netapp_cluster = db.get(NetAppCluster, run.netapp_cluster_id)
                if cluster is None or netapp_cluster is None:
                    raise RuntimeError("Hyper-V-Cluster oder NetApp-System nicht mehr vorhanden")
                netapp = _netapp_service_for(netapp_cluster)
                password = decrypt_secret(cluster.encrypted_password)
                cno = _Cluster(cluster, password)
                if any(c.name.lower() == run.csv_name.lower() for c in cno.service.list_csvs(cno.session)):
                    raise RuntimeError(f"Eine CSV '{run.csv_name}' gibt es im Cluster bereits")
                if cno.service.cluster_resource_exists(cno.session, run.csv_name):
                    raise RuntimeError(f"Eine Cluster-Ressource '{run.csv_name}' gibt es bereits")
                if netapp.volume_exists(run.svm_name, run.volume_name):
                    raise RuntimeError(f"Volume '{run.volume_name}' gibt es auf SVM '{run.svm_name}' bereits")
                known = {(i["svm_name"], i["name"]) for i in netapp.csv_create_igroups()}
                missing = [g for g in opts.get("igroup_names", []) if (run.svm_name, g) not in known]
                if missing:
                    raise RuntimeError(f"Initiator-Gruppe(n) auf SVM '{run.svm_name}' nicht gefunden: {', '.join(missing)}")
                nodes = cno.service.list_cluster_nodes(cno.session)
                node_ips = cno.service.node_address_map(cno.session)
                active = sorted((n.name for n in nodes if n.state in ("Up", "Paused")), key=str.lower)
                down = [n.name for n in nodes if n.state not in ("Up", "Paused")]
                if not active:
                    raise RuntimeError("Kein Cluster-Knoten ist betriebsbereit")
                ctx.row.message = f"{len(active)} Knoten bereit" + (f", nicht erreichbar: {', '.join(down)}" if down else "")

            with _StepCtx(db, run.id, "volume", f"Volume {run.volume_name} anlegen", step_model=CsvCreateRunStep) as ctx:
                run.created_volume_uuid = netapp.create_csv_volume(
                    run.svm_name, run.volume_name, opts.get("aggregate_name"), run.volume_size_bytes,
                    autosize_grow=bool(opts.get("autosize_grow")),
                )
                ctx.row.message = (
                    f"{_gb(run.volume_size_bytes)} auf {opts.get('aggregate_name') or 'von ONTAP gewähltem Aggregat'}, "
                    "thin, Snapshot-Policy none, Reserve 0 %" + (", Autosize grow" if opts.get("autosize_grow") else "")
                )

            with _StepCtx(db, run.id, "lun", f"LUN {run.lun_name} anlegen", step_model=CsvCreateRunStep) as ctx:
                lun = netapp.create_csv_lun(run.svm_name, run.volume_name, run.lun_name, run.lun_size_bytes)
                run.created_lun_uuid = lun["uuid"]
                run.lun_serial_number = (lun["serial_number"] or "").strip() or None
                if not run.lun_serial_number:
                    raise RuntimeError("ONTAP liefert keine Seriennummer für die neue LUN")
                ctx.row.message = f"{_gb(run.lun_size_bytes)}, hyper_v, thin, Space Allocation an, Seriennummer {run.lun_serial_number}"
            lun_path = f"/vol/{run.volume_name}/{run.lun_name}"

            with _StepCtx(db, run.id, "map", "Auf Initiator-Gruppe(n) mappen", step_model=CsvCreateRunStep) as ctx:
                for igroup in opts.get("igroup_names", []):
                    assigned = netapp.map_lun(run.svm_name, lun_path, igroup, run.lun_id)
                    if run.lun_id is None:
                        run.lun_id = assigned
                    run.mapped_igroups = [*run.mapped_igroups, igroup]
                    db.commit()
                ctx.row.message = f"{', '.join(run.mapped_igroups)} (LUN-ID {run.lun_id})"

            disk_size = run.lun_size_bytes
            with _StepCtx(db, run.id, "rescan", "Datenträger auf allen Knoten einlesen", step_model=CsvCreateRunStep) as ctx:
                sessions: dict[str, tuple[HyperVService, object]] = {}
                pending = list(active)
                deadline = time.monotonic() + _DISK_WAIT_SEC
                errors: dict[str, str] = {}
                while pending:
                    for name in list(pending):
                        try:
                            if name not in sessions:
                                sessions[name] = cno.node(name, node_ips)
                            service, session = sessions[name]
                            service.rescan_storage(session)
                            disk = service.find_disk_by_serial(session, run.lun_serial_number)
                            if disk:
                                pending.remove(name)
                                errors.pop(name, None)
                                if name == active[0]:
                                    disk_size = disk["size_bytes"]
                        except Exception as exc:  # noqa: BLE001
                            errors[name] = str(exc)[:200]
                    if not pending or time.monotonic() > deadline:
                        break
                    time.sleep(10)
                if pending:
                    detail = "; ".join(f"{n}: {errors.get(n, 'LUN nicht sichtbar')}" for n in pending)
                    raise RuntimeError(
                        f"Nicht alle Knoten sehen die neue LUN ({detail}). Sind die Initiatoren dieser Knoten in den "
                        "gewählten igroups, und sind iSCSI-Sitzungen bzw. FC-Zoning vorhanden?"
                    )
                ctx.row.message = f"Alle {len(active)} Knoten sehen die LUN ({_gb(disk_size)})"

            run.format_node = active[0]
            with _StepCtx(db, run.id, "format", f"Auf {run.format_node} initialisieren und formatieren", step_model=CsvCreateRunStep) as ctx:
                service, session = sessions[run.format_node]
                result = service.initialize_and_format_disk(
                    session, run.lun_serial_number, file_system=opts.get("file_system", "NTFS"),
                    allocation_unit=int(opts.get("allocation_unit", 65536)), label=run.csv_name,
                )
                run.disk_formatted = True
                disk_guid = result["disk_guid"]
                ctx.row.message = (
                    f"Disk {result['disk_number']}, GPT, {opts.get('file_system', 'NTFS')} "
                    f"{int(opts.get('allocation_unit', 65536)) // 1024} KB, Partition {_gb(result['partition_size_bytes'])}"
                    + (f" ({result['paths']} Disk-Objekte mit dieser Seriennummer, MPIO prüfen)" if result["paths"] > 1 else "")
                )

            with _StepCtx(db, run.id, "cluster-disk", "Als Cluster-Disk aufnehmen", step_model=CsvCreateRunStep) as ctx:
                name, note = cno.call(lambda s, sess: s.add_cluster_disk(sess, disk_guid, disk_size, run.csv_name))
                run.cluster_resource_name = name or run.csv_name
                ctx.row.message = f"Ressource '{run.cluster_resource_name}'{note}"

            with _StepCtx(db, run.id, "csv", "Zur CSV machen", step_model=CsvCreateRunStep) as ctx:
                path, note = cno.call(lambda s, sess: s.add_cluster_shared_volume(sess, run.cluster_resource_name))
                run.csv_added = True
                run.csv_path = path or None
                ctx.row.message = f"{path}{note}"

            warnings: list[str] = []
            if opts.get("rename_folder"):
                def _rename() -> str:
                    path, note = cno.call(lambda s, sess: s.rename_csv_folder(sess, run.cluster_resource_name, run.csv_name))
                    run.csv_path = path or run.csv_path
                    return f"{path}{note}"

                _soft_step(db, run.id, "rename-folder", "Mount-Ordner umbenennen", _rename, warnings)

            def _inventory() -> str:
                notes = []
                try:
                    _discover_and_persist(db, netapp_cluster)
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"NetApp-Discovery fehlgeschlagen: {exc}")
                csvs = cno.service.list_csvs(cno.session)
                _refresh_csv_rows(db, run.hyperv_cluster_id, csvs)
                row = db.query(HyperVCsv).filter(
                    HyperVCsv.cluster_id == run.hyperv_cluster_id, HyperVCsv.name == run.csv_name,
                ).first()
                if row is None:
                    raise RuntimeError("Die neue CSV taucht in der Cluster-Abfrage nicht auf")
                site = SiteResolver(db).csv_site(row)[0]
                text = f"{_gb(row.capacity_bytes)}, LUN {'zugeordnet' if row.netapp_lun_id else 'NICHT zugeordnet'}"
                if site:
                    text += f", Standort {site.name}"
                if notes:
                    raise RuntimeError(f"{text}; " + "; ".join(notes))
                return text

            _soft_step(db, run.id, "inventory", "Inventory aktualisieren", _inventory, warnings)

            group_id = opts.get("resource_group_id")
            if group_id:
                def _assign() -> str:
                    group = db.get(ResourceGroup, group_id)
                    if group is None:
                        raise RuntimeError("Protection Group nicht mehr vorhanden")
                    key = make_member_key(run.hyperv_cluster_id, run.csv_name)
                    if key not in (group.members or []):
                        group.members = [*(group.members or []), key]
                        db.commit()
                    text = f"'{group.name}'"
                    if any(p.snapmirror_update for p in group.policies):
                        text += (
                            f" -- Hinweis: eine verknüpfte Policy repliziert per SnapMirror, für Volume "
                            f"{run.volume_name} gibt es aber noch keine SnapMirror-Beziehung"
                        )
                    return text

                _soft_step(db, run.id, "protection-group", "Protection Group zuordnen", _assign, warnings)

            run.status = RestoreStatus.SUCCEEDED
            run.error_message = ("Mit Warnungen: " + " | ".join(warnings))[:2000] if warnings else None
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"CSV '{run.csv_name}' angelegt ({_gb(run.lun_size_bytes)}, LUN {lun_path}, Volume {run.volume_name} "
                f"auf {run.svm_name}, igroups {', '.join(run.mapped_igroups)}) (durch {run.requested_by})"
                + (f" -- Warnungen: {' | '.join(warnings)}" if warnings else ""),
                level="WARNING" if warnings else "INFO",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(CsvCreateRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            run.error_message = str(detail)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"CSV '{run.csv_name}' anlegen fehlgeschlagen: {detail} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()


def _execute_rollback(run_id: str) -> None:  # noqa: C901
    """Entfernt in umgekehrter Reihenfolge genau das, was der Lauf angelegt
    hat. Jeder erfolgreiche Schritt loescht seinen Merker -- ein erneutes
    Zurueckrollen nach einem Teilfehler setzt dort wieder an."""
    db = SessionLocal()
    failures: list[str] = []
    try:
        run = db.get(CsvCreateRun, run_id)
        if run is None:
            return
        cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
        netapp_cluster = db.get(NetAppCluster, run.netapp_cluster_id)
        password = decrypt_secret(cluster.encrypted_password) if cluster else None

        def step(step_id: str, label: str, fn) -> None:
            with _StepCtx(db, run.id, step_id, label, step_model=CsvCreateRunStep) as ctx:
                try:
                    ctx.row.message = fn() or None
                except Exception as exc:  # noqa: BLE001
                    ctx.row.message = f"Fehlgeschlagen: {exc}"[:2000]
                    failures.append(f"{label}: {exc}")
            if ctx.row.message and ctx.row.message.startswith("Fehlgeschlagen:"):
                ctx.row.status = RestoreStepStatus.ERROR
            db.commit()

        cno: _Cluster | None = None

        def get_cno() -> _Cluster:
            nonlocal cno
            if cno is None:
                if cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht mehr vorhanden")
                cno = _Cluster(cluster, password)
            return cno

        if run.csv_added and run.cluster_resource_name:
            def _remove_csv() -> str:
                _, note = get_cno().call(lambda s, sess: s.remove_cluster_shared_volume(sess, run.cluster_resource_name))
                run.csv_added = False
                return f"'{run.cluster_resource_name}' ist keine CSV mehr{note}"

            step("rb-csv", "Zurückrollen: CSV entfernen", _remove_csv)

        if run.cluster_resource_name and not run.csv_added:
            def _remove_disk() -> str:
                name = run.cluster_resource_name
                _, note = get_cno().call(lambda s, sess: s.remove_cluster_resource(sess, name))
                run.cluster_resource_name = None
                return f"Cluster-Disk '{name}' entfernt{note}"

            step("rb-cluster-disk", "Zurückrollen: Cluster-Disk entfernen", _remove_disk)

        if run.disk_formatted and not run.cluster_resource_name and run.format_node and run.lun_serial_number:
            def _offline() -> str:
                c = get_cno()
                service, session = c.node(run.format_node, c.service.node_address_map(c.session))
                service.set_disk_offline_by_serial(session, run.lun_serial_number)
                run.disk_formatted = False
                return f"Disk auf {run.format_node} offline"

            step("rb-disk-offline", "Zurückrollen: Disk offline nehmen", _offline)

        netapp = _netapp_service_for(netapp_cluster) if netapp_cluster else None
        if run.mapped_igroups and not run.csv_added and not run.cluster_resource_name:
            def _unmap() -> str:
                if netapp is None or not run.created_lun_uuid:
                    raise RuntimeError("NetApp-System oder LUN nicht mehr bekannt")
                removed = netapp.unmap_lun_everywhere(run.created_lun_uuid)
                run.mapped_igroups = []
                return f"Mapping entfernt: {', '.join(removed) or '–'}"

            step("rb-unmap", "Zurückrollen: LUN-Mapping entfernen", _unmap)

        if run.created_lun_uuid and not run.mapped_igroups:
            def _delete_lun() -> str:
                netapp.delete_lun(run.created_lun_uuid)
                run.created_lun_uuid = None
                return f"/vol/{run.volume_name}/{run.lun_name} gelöscht"

            step("rb-lun", "Zurückrollen: LUN löschen", _delete_lun)

        if run.created_volume_uuid and not run.created_lun_uuid:
            def _delete_volume() -> str:
                netapp.delete_volume_forced(run.created_volume_uuid)
                run.created_volume_uuid = None
                return f"{run.volume_name} gelöscht"

            step("rb-volume", "Zurückrollen: Volume löschen", _delete_volume)

        if cluster is not None and run.lun_serial_number and not run.created_lun_uuid:
            def _rescan() -> str:
                c = get_cno()
                node_ips = c.service.node_address_map(c.session)
                done = 0
                for node in c.service.list_cluster_nodes(c.session):
                    if node.state not in ("Up", "Paused"):
                        continue
                    try:
                        service, session = c.node(node.name, node_ips)
                        service.rescan_storage(session)
                        done += 1
                    except Exception:  # noqa: BLE001
                        pass
                return f"{done} Knoten neu eingelesen"

            step("rb-rescan", "Zurückrollen: Datenträger neu einlesen", _rescan)

        if netapp_cluster is not None and not failures:
            try:
                _discover_and_persist(db, netapp_cluster)
            except Exception:  # noqa: BLE001
                pass

        run = db.get(CsvCreateRun, run_id)
        run.finished_at = datetime.now(timezone.utc)
        if failures or run.has_created_objects:
            run.status = RestoreStatus.FAILED
            run.error_message = ("Zurückrollen unvollständig: " + " | ".join(failures or ["Objekte übrig"]))[:2000]
            db.commit()
            _log(db, f"CSV '{run.csv_name}' anlegen: {run.error_message}", level="ERROR")
        else:
            run.status = RestoreStatus.CLEANED_UP
            db.commit()
            _log(db, f"CSV '{run.csv_name}' anlegen: vollständig zurückgerollt")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(CsvCreateRun, run_id)
        if run is not None:
            run.status = RestoreStatus.FAILED
            run.error_message = f"Zurückrollen abgebrochen: {exc}"[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
