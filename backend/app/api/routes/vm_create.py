"""Neue VM per Assistent anlegen (Backlog #74, Start 2026-10-02): Button
"Neue VM" in Inventory > VMs. Hintergrund-Task mit Schritt-Protokoll:

  Vorpruefung -> VM anlegen -> CPU/RAM/Checkpoint-Typ -> Festplatte(n) ->
  Netzwerk -> Firmware + Installationsmedium -> Cluster-Rolle -> (starten)
  -> Inventory -> (Protection Group)

Ablage: CSV oder SMB3-Freigabe; New-VM legt darunter den Ordner <VM-Name>
an (Konfiguration), die Disks kommen nach <VM-Name>\\Virtual Hard Disks --
dieselbe Struktur, die Storage-Move und Restore erwarten. Standardwerte:
Generation 2, Secure Boot an (Vorlage waehlbar), vTPM optional (lokaler
Schluesselschutz -- die VM startet damit nur auf Knoten, die das Zertifikat
kennen), Checkpoint-Typ Production, hochverfuegbar.

Liegt Ablage oder ISO auf einer SMB3-Freigabe, laufen die Schritte auf dem
Knoten in einer CredSSP-Sitzung (echter Double-Hop, wie beim SMB3-Restore).
Cluster-Rolle mit CredSSP-Rueckfall wie beim Recreate.

Fehler bis einschliesslich Cluster-Rolle: Rueckfrage Zurueckrollen/Behalten
(wie CSV anlegen). Zurueckrollen entfernt Cluster-Rolle, VM und den vom Lauf
angelegten Ordner <Ablage>\\<VM-Name> -- der vorher nicht existieren durfte.
Starten, Inventory und Protection Group gelten nur als Warnung."""

import copy
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_user_permissions, require_permission
from app.api.routes.csv_create import _Cluster, _hyperv_service, _soft_step
from app.api.routes.hyperv_clusters import _run_discovery as _run_hyperv_discovery
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.restore import _StepCtx
from app.api.routes.smb_delete import _volume_relative
from app.api.routes.smb_resize import _cifs_share_for
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import SiteResolver, normalize_node_name
from app.db.session import SessionLocal, get_db
from app.models.backup_policy import BackupScope
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVSmbShare, HyperVVm
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppVolume
from app.models.resource_group import ResourceGroup, make_member_key
from app.models.restore_run import RestoreStatus, RestoreStepStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_create_run import VmCreateRun, VmCreateRunStep
from app.schemas.site import SiteBadge

router = APIRouter(prefix="/api/vm-create", tags=["vm-create"])

GIB = 1024**3
MIB = 1024**2
_VM_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,61}[A-Za-z0-9_-])?$")
_SECURE_BOOT_TEMPLATES = ("MicrosoftWindows", "MicrosoftUEFICertificateAuthority")
_manage = require_permission(Permission.HYPERV_MANAGE)


# --- Schemas --------------------------------------------------------------------------


class NodeOption(BaseModel):
    name: str
    state: str
    site: SiteBadge | None = None
    vm_count: int = 0
    memory_total_bytes: int | None = None
    memory_free_bytes: int | None = None
    switches: list[dict] = []
    error: str | None = None


class LocationOption(BaseModel):
    kind: Literal["csv", "smb"]
    # CSV-Name bzw. "server|share"
    key: str
    label: str
    root: str
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    site: SiteBadge | None = None


class VmCreateOptions(BaseModel):
    cluster_id: str
    nodes: list[NodeOption]
    locations: list[LocationOption]
    vm_names: list[str]
    busy_reason: str | None = None


class IsoFile(BaseModel):
    path: str
    size_bytes: int


class IsoList(BaseModel):
    isos: list[IsoFile]
    warnings: list[str] = []


class DataDisk(BaseModel):
    size_bytes: int = Field(ge=GIB)
    dynamic: bool = True


class VmCreateRequest(BaseModel):
    cluster_id: str
    vm_name: str
    node_name: str
    location_kind: Literal["csv", "smb"]
    location_key: str
    generation: Literal[1, 2] = 2
    cpu_count: int = Field(default=2, ge=1, le=240)
    memory_startup_bytes: int = Field(ge=512 * MIB)
    dynamic_memory: bool = False
    memory_minimum_bytes: int | None = None
    memory_maximum_bytes: int | None = None
    disk_size_bytes: int = Field(ge=GIB)
    disk_dynamic: bool = True
    data_disks: list[DataDisk] = Field(default_factory=list, max_length=8)
    switch_name: str | None = None
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    secure_boot: bool = True
    secure_boot_template: str = "MicrosoftWindows"
    tpm: bool = False
    iso_path: str | None = None
    high_availability: bool = True
    start_after: bool = False
    resource_group_id: str | None = None


class StepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class VmCreateRunRead(BaseModel):
    id: str
    vm_name: str
    node_name: str
    storage_root: str
    vm_folder: str
    new_vm_uuid: str | None = None
    vm_created: bool = False
    cluster_role_added: bool = False
    rollback_declined: bool = False
    has_created_objects: bool = False
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[StepRead]

    class Config:
        from_attributes = True


# --- Hilfen ---------------------------------------------------------------------------


def _gb(value: int | None) -> str:
    return f"{(value or 0) / GIB:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="vm-create", message=message))
    db.commit()


def _badge(site) -> SiteBadge | None:
    return SiteBadge.model_validate(site) if site else None


def _busy_reason(db: Session, cluster_id: str, vm_name: str | None = None, own_run_id: str | None = None) -> str | None:
    query = db.query(VmCreateRun).filter(
        VmCreateRun.hyperv_cluster_id == cluster_id, VmCreateRun.status == RestoreStatus.RUNNING,
        VmCreateRun.id != (own_run_id or ""),
    )
    for run in query:
        if vm_name is None or run.vm_name.lower() == vm_name.lower():
            return f"Die VM '{run.vm_name}' wird gerade angelegt bzw. zurückgerollt -- bitte das Ende abwarten."
    return None


def _get_cluster(db: Session, cluster_id: str) -> HyperVCluster:
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    return cluster


def _locations(db: Session, cluster_id: str) -> list[LocationOption]:
    resolver = SiteResolver(db)
    result = []
    for csv in db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).order_by(HyperVCsv.name):
        if not csv.path:
            continue
        result.append(LocationOption(
            kind="csv", key=csv.name, label=f"CSV {csv.name}", root=csv.path.rstrip("\\"),
            capacity_bytes=csv.capacity_bytes, used_bytes=csv.used_bytes, site=_badge(resolver.csv_site(csv)[0]),
        ))
    for share in db.query(HyperVSmbShare).filter(HyperVSmbShare.cluster_id == cluster_id).order_by(HyperVSmbShare.share):
        result.append(LocationOption(
            kind="smb", key=f"{share.server}|{share.share}", label=f"SMB3 \\\\{share.server}\\{share.share}",
            root=f"\\\\{share.server}\\{share.share}", capacity_bytes=share.capacity_bytes, used_bytes=share.used_bytes,
            site=_badge(resolver.smb_share_site(share)),
        ))
    return result


def _node_session(cluster: HyperVCluster, password: str, cno: _Cluster, node_ips: dict[str, str], name: str, *, credssp: bool, timeout: int = 300):
    settings = cno.settings
    if credssp and settings.winrm_transport != "credssp":
        settings = copy.copy(settings)
        settings.winrm_transport = "credssp"
    service = _hyperv_service(cluster, node_ips.get(name.lower(), name), name, settings=settings)
    return service, service.connect(cluster.username, password, read_timeout_sec=timeout + 60, operation_timeout_sec=timeout)


# --- Auswahl-Optionen (live) ---------------------------------------------------------


@router.get("/options/{cluster_id}", response_model=VmCreateOptions)
def get_options(cluster_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmCreateOptions:
    """Knoten mit Status, Standort, freiem RAM und ihren virtuellen Switches;
    dazu die moeglichen Ablageorte (CSVs, SMB3-Freigaben) aus dem Inventory."""
    cluster = _get_cluster(db, cluster_id)
    password = decrypt_secret(cluster.encrypted_password)
    try:
        cno = _Cluster(cluster, password)
        nodes = cno.service.list_cluster_nodes(cno.session)
        node_ips = cno.service.node_address_map(cno.session)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc

    def _one(name: str):
        service, session = _node_session(cluster, password, cno, node_ips, name, credssp=False, timeout=20)
        return service.node_memory(session), service.list_vm_switches(session)

    active = [n.name for n in nodes if n.state == "Up"]
    live: dict[str, tuple] = {}
    errors: dict[str, str] = {}
    if active:
        with ThreadPoolExecutor(max_workers=min(8, len(active))) as pool:
            futures = {pool.submit(_one, name): name for name in active}
            for future, name in futures.items():
                try:
                    live[name] = future.result(timeout=60)
                except Exception as exc:  # noqa: BLE001 -- nur Anzeige
                    errors[name] = str(exc)[:300]

    resolver = SiteResolver(db)
    vms = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id).all()
    counts: dict[str, int] = {}
    for vm in vms:
        counts[normalize_node_name(vm.host_name)] = counts.get(normalize_node_name(vm.host_name), 0) + 1
    result = []
    for node in sorted(nodes, key=lambda n: n.name.lower()):
        memory, switches = live.get(node.name, ((None, None), []))
        result.append(NodeOption(
            name=node.name, state=node.state, site=_badge(resolver.node_site(cluster_id, node.name)),
            vm_count=counts.get(normalize_node_name(node.name), 0), memory_total_bytes=memory[0], memory_free_bytes=memory[1],
            switches=switches, error=errors.get(node.name),
        ))
    return VmCreateOptions(
        cluster_id=cluster_id, nodes=result, locations=_locations(db, cluster_id), vm_names=sorted(v.name for v in vms),
        busy_reason=None,
    )


@router.get("/isos/{cluster_id}", response_model=IsoList)
def list_isos(cluster_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> IsoList:
    """*.iso auf allen CSVs (per WinRM) und SMB3-Freigaben (per ONTAP-Datei-
    API, kein Double-Hop), jeweils bis 3 Ordnerebenen tief."""
    cluster = _get_cluster(db, cluster_id)
    isos: list[IsoFile] = []
    warnings: list[str] = []
    try:
        cno = _Cluster(cluster, decrypt_secret(cluster.encrypted_password))
        isos += [IsoFile(**f) for f in cno.service.find_iso_files(cno.session)]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"CSVs konnten nicht durchsucht werden: {str(exc)[:200]}")
    for share in db.query(HyperVSmbShare).filter(HyperVSmbShare.cluster_id == cluster_id):
        try:
            cifs = _cifs_share_for(db, share.server, share.share)
            volume = db.query(NetAppVolume).filter(
                NetAppVolume.cluster_id == cifs.cluster_id, NetAppVolume.svm_name == cifs.svm_name, NetAppVolume.name == cifs.volume_name,
            ).first() if cifs else None
            if cifs is None or volume is None or not volume.uuid:
                continue
            netapp = _netapp_service_for(db.get(NetAppCluster, cifs.cluster_id))
            base = _volume_relative(cifs.path, netapp.volume_space(volume.uuid)["junction_path"]).strip("/")
            scan = netapp.volume_file_scan(volume.uuid, base or "/", limit=100, suffixes=(".iso",), max_depth=3)
            for f in scan["files"]:
                relative = f["path"].lstrip("/")
                if base and relative.lower().startswith(base.lower() + "/"):
                    relative = relative[len(base) + 1:]
                isos.append(IsoFile(path=f"\\\\{share.server}\\{share.share}\\" + relative.replace("/", "\\"), size_bytes=f["size_bytes"]))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"\\\\{share.server}\\{share.share} konnte nicht durchsucht werden: {str(exc)[:200]}")
    isos.sort(key=lambda i: i.path.lower())
    return IsoList(isos=isos, warnings=warnings)


# --- Start / Rueckfrage ----------------------------------------------------------------


def _validate(db: Session, payload: VmCreateRequest, user) -> LocationOption:
    errors = []
    if not _VM_NAME_RE.match(payload.vm_name):
        errors.append("VM-Name: Buchstaben, Ziffern, Leerzeichen, '_', '-', '.' (max. 63 Zeichen, kein Leerzeichen/Punkt am Ende).")
    if db.query(HyperVVm).filter(HyperVVm.cluster_id == payload.cluster_id, HyperVVm.name.ilike(payload.vm_name)).first():
        errors.append(f"Eine VM '{payload.vm_name}' gibt es auf diesem Cluster bereits.")
    location = next(
        (l for l in _locations(db, payload.cluster_id) if l.kind == payload.location_kind and l.key == payload.location_key), None,
    )
    if location is None:
        errors.append("Der gewählte Ablageort ist nicht (mehr) bekannt.")
    if payload.dynamic_memory:
        low, high = payload.memory_minimum_bytes, payload.memory_maximum_bytes
        if not low or not high or not (low <= payload.memory_startup_bytes <= high):
            errors.append("Dynamischer RAM: Minimum ≤ Start-RAM ≤ Maximum angeben.")
    if payload.generation == 2 and payload.secure_boot and payload.secure_boot_template not in _SECURE_BOOT_TEMPLATES:
        errors.append("Unbekannte Secure-Boot-Vorlage.")
    if payload.tpm and payload.generation != 2:
        errors.append("vTPM gibt es nur für Generation-2-VMs.")
    if payload.iso_path:
        iso = payload.iso_path.strip()
        if not iso.lower().endswith(".iso") or not (iso.startswith("\\\\") or re.match(r"^[A-Za-z]:\\ClusterStorage\\", iso)):
            errors.append("Das Installationsmedium muss eine .iso-Datei auf einer CSV oder SMB3-Freigabe sein.")
    if payload.resource_group_id:
        group = db.get(ResourceGroup, payload.resource_group_id)
        if group is None or group.scope != BackupScope.VM:
            errors.append("Die gewählte Protection Group gibt es nicht oder sie enthält keine VMs.")
        elif Permission.BACKUP_CREATE not in get_user_permissions(user, db):
            errors.append("Für die Zuordnung zu einer Protection Group fehlt die Berechtigung backup:create.")
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))
    return location


@router.get("/runs/{run_id}", response_model=VmCreateRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmCreateRun:
    run = db.get(VmCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.post("", response_model=VmCreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_create(
    payload: VmCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(_manage),
) -> VmCreateRun:
    _get_cluster(db, payload.cluster_id)
    payload.vm_name = payload.vm_name.strip()
    location = _validate(db, payload, user)
    busy = _busy_reason(db, payload.cluster_id, payload.vm_name)
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    options = payload.model_dump(exclude={"cluster_id", "vm_name", "node_name"})
    options["location_label"] = location.label
    run = VmCreateRun(
        hyperv_cluster_id=payload.cluster_id, vm_name=payload.vm_name, node_name=payload.node_name,
        storage_root=location.root, vm_folder=f"{location.root}\\{payload.vm_name}", options=options,
        requested_by=user.display_name or user.username, status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_create, run.id)
    return run


@router.post("/runs/{run_id}/rollback", response_model=VmCreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_rollback(run_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(_manage)) -> VmCreateRun:
    run = db.get(VmCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status != RestoreStatus.FAILED or not run.has_created_objects or run.rollback_declined:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Für diesen Lauf gibt es nichts zurückzurollen.")
    run.status = RestoreStatus.RUNNING
    run.finished_at = None
    db.commit()
    _log(db, f"VM '{run.vm_name}' anlegen: Zurückrollen gestartet (durch {user.display_name or user.username})")
    background_tasks.add_task(_execute_rollback, run.id)
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/keep", response_model=VmCreateRunRead)
def keep_objects(run_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmCreateRun:
    run = db.get(VmCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status == RestoreStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Lauf ist noch aktiv.")
    run.rollback_declined = True
    db.commit()
    _log(db, f"VM '{run.vm_name}' anlegen: angelegte VM nach Fehler bewusst behalten (durch {user.display_name or user.username})")
    return run


# --- Ausfuehrung ----------------------------------------------------------------------


def _uses_unc(run: VmCreateRun) -> bool:
    return run.storage_root.startswith("\\\\") or str((run.options or {}).get("iso_path") or "").startswith("\\\\")


def _execute_create(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    try:
        run = db.get(VmCreateRun, run_id)
        if run is None:
            return
        opts = run.options or {}
        name = run.vm_name
        iso = (opts.get("iso_path") or "").strip() or None
        generation = int(opts.get("generation") or 2)
        credssp = _uses_unc(run)
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=VmCreateRunStep) as ctx:
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                if cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht mehr vorhanden")
                password = decrypt_secret(cluster.encrypted_password)
                cno = _Cluster(cluster, password)
                nodes = {n.name.lower(): n for n in cno.service.list_cluster_nodes(cno.session)}
                target = nodes.get(run.node_name.lower())
                if target is None or target.state != "Up":
                    raise RuntimeError(f"Knoten '{run.node_name}' ist nicht betriebsbereit")
                if cno.service.vm_or_group_exists(cno.session, name):
                    raise RuntimeError(f"Im Cluster gibt es bereits eine VM oder Rolle '{name}'")
                node_ips = cno.service.node_address_map(cno.session)
                # Fest angelegte Disks koennen lange dauern -- grosszuegiges Zeitlimit.
                node, session = _node_session(cluster, password, cno, node_ips, target.name, credssp=credssp, timeout=3600)
                if node.existing_paths(session, [run.vm_folder]):
                    raise RuntimeError(f"Der Ordner {run.vm_folder} existiert bereits")
                if cno.service.vm_or_group_exists(session, name):
                    raise RuntimeError(f"Auf {target.name} gibt es bereits eine VM '{name}'")
                if iso and not node.existing_paths(session, [iso]):
                    raise RuntimeError(f"Das Installationsmedium {iso} ist von {target.name} aus nicht erreichbar")
                switch = opts.get("switch_name")
                if switch and switch not in [s["name"] for s in node.list_vm_switches(session)]:
                    raise RuntimeError(f"Der virtuelle Switch '{switch}' existiert auf {target.name} nicht")
                ctx.row.message = f"{target.name}, {run.vm_folder}" + (" (SMB3: CredSSP)" if credssp else "")

            with _StepCtx(db, run.id, "create-vm", "VM anlegen", step_model=VmCreateRunStep) as ctx:
                run.new_vm_uuid = node.create_vm(session, name, generation, run.storage_root)
                run.vm_created = True
                ctx.row.message = f"Generation {generation}, ID {run.new_vm_uuid}"

            with _StepCtx(db, run.id, "hardware", "CPU, RAM und Checkpoint-Typ einstellen", step_model=VmCreateRunStep) as ctx:
                dynamic = bool(opts.get("dynamic_memory"))
                node.configure_new_vm(
                    session, name, cpu_count=int(opts.get("cpu_count") or 2), memory_startup_bytes=int(opts["memory_startup_bytes"]),
                    memory_minimum_bytes=opts.get("memory_minimum_bytes") if dynamic else None,
                    memory_maximum_bytes=opts.get("memory_maximum_bytes") if dynamic else None,
                )
                ctx.row.message = (
                    f"{opts.get('cpu_count')} vCPU, {_gb(opts['memory_startup_bytes'])} RAM"
                    + (f" (dynamisch {_gb(opts.get('memory_minimum_bytes'))}–{_gb(opts.get('memory_maximum_bytes'))})" if dynamic else "")
                    + ", Checkpoint-Typ Production"
                )

            with _StepCtx(db, run.id, "disks", "Festplatte(n) anlegen", step_model=VmCreateRunStep) as ctx:
                disk_dir = f"{run.vm_folder}\\Virtual Hard Disks"
                disks = [(f"{disk_dir}\\{name}.vhdx", int(opts["disk_size_bytes"]), bool(opts.get("disk_dynamic", True)))]
                for index, data in enumerate(opts.get("data_disks") or [], start=1):
                    disks.append((f"{disk_dir}\\{name}_data{index}.vhdx", int(data["size_bytes"]), bool(data.get("dynamic", True))))
                for path, size, is_dynamic in disks:
                    node.create_and_attach_vhd(session, name, path, size, is_dynamic)
                ctx.row.message = ", ".join(f"{p.rsplit(chr(92), 1)[-1]} {_gb(s)} {'dynamisch' if d else 'fest'}" for p, s, d in disks)

            switch = opts.get("switch_name")
            if switch:
                with _StepCtx(db, run.id, "network", "Netzwerk verbinden", step_model=VmCreateRunStep) as ctx:
                    node.connect_default_adapter(session, name, switch, opts.get("vlan_id"))
                    ctx.row.message = switch + (f", VLAN {opts['vlan_id']}" if opts.get("vlan_id") else "")

            with _StepCtx(db, run.id, "boot", "Firmware und Installationsmedium", step_model=VmCreateRunStep) as ctx:
                node.configure_new_vm_boot(
                    session, name, generation=generation, secure_boot=bool(opts.get("secure_boot", True)),
                    secure_boot_template=opts.get("secure_boot_template") or _SECURE_BOOT_TEMPLATES[0],
                    tpm=bool(opts.get("tpm")), iso_path=iso,
                )
                parts = []
                if generation == 2:
                    parts.append(f"Secure Boot {'an (' + str(opts.get('secure_boot_template')) + ')' if opts.get('secure_boot', True) else 'aus'}")
                    if opts.get("tpm"):
                        parts.append("vTPM (lokaler Schlüsselschutz)")
                parts.append(f"ISO {iso}, bootet zuerst von DVD" if iso else "kein Installationsmedium")
                ctx.row.message = ", ".join(parts)

            if opts.get("high_availability", True):
                with _StepCtx(db, run.id, "cluster-role", "Als Cluster-Rolle registrieren", step_model=VmCreateRunStep) as ctx:
                    _, note = cno.call(lambda s, sess: s.register_cluster_role(sess, name))
                    run.cluster_role_added = True
                    ctx.row.message = f"hochverfügbar{note}"

            warnings: list[str] = []
            if opts.get("start_after"):
                _soft_step(
                    db, run.id, "start", "VM starten", lambda: f"Status {node.power_vm(session, name, 'start')}", warnings,
                    step_model=VmCreateRunStep,
                )

            def _inventory() -> str:
                _run_hyperv_discovery(db, cluster)
                row = db.query(HyperVVm).filter(HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == name).first()
                if row is None:
                    raise RuntimeError("Die neue VM taucht nach der Discovery nicht im Inventory auf")
                return f"Discovery abgeschlossen, VM auf {row.host_name}"

            _soft_step(db, run.id, "inventory", "Inventory aktualisieren", _inventory, warnings, step_model=VmCreateRunStep)

            group_id = opts.get("resource_group_id")
            if group_id:
                def _assign() -> str:
                    group = db.get(ResourceGroup, group_id)
                    if group is None:
                        raise RuntimeError("Protection Group nicht mehr vorhanden")
                    key = make_member_key(run.hyperv_cluster_id, name)
                    if key not in (group.members or []):
                        group.members = [*(group.members or []), key]
                        db.commit()
                    return f"'{group.name}'"

                _soft_step(db, run.id, "protection-group", "Protection Group zuordnen", _assign, warnings, step_model=VmCreateRunStep)

            run = db.get(VmCreateRun, run_id)
            run.status = RestoreStatus.SUCCEEDED
            run.error_message = ("Mit Warnungen: " + " | ".join(warnings))[:2000] if warnings else None
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"VM '{name}' angelegt auf {run.node_name} unter {run.vm_folder} (durch {run.requested_by})"
                + (f" -- Warnungen: {' | '.join(warnings)}" if warnings else ""),
                level="WARNING" if warnings else "INFO",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(VmCreateRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            run.error_message = str(detail)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"VM '{run.vm_name}' anlegen fehlgeschlagen: {detail} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()


def _execute_rollback(run_id: str) -> None:
    db = SessionLocal()
    failures: list[str] = []
    try:
        run = db.get(VmCreateRun, run_id)
        if run is None:
            return
        cluster = db.get(HyperVCluster, run.hyperv_cluster_id)

        def step(step_id: str, label: str, fn) -> None:
            with _StepCtx(db, run.id, step_id, label, step_model=VmCreateRunStep) as ctx:
                try:
                    ctx.row.message = fn() or None
                except Exception as exc:  # noqa: BLE001
                    ctx.row.message = f"Fehlgeschlagen: {exc}"[:2000]
                    failures.append(f"{label}: {exc}")
            if ctx.row.message and ctx.row.message.startswith("Fehlgeschlagen:"):
                ctx.row.status = RestoreStepStatus.ERROR
            db.commit()

        cno_box: list[_Cluster] = []

        def get_cno() -> _Cluster:
            if not cno_box:
                if cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht mehr vorhanden")
                cno_box.append(_Cluster(cluster, decrypt_secret(cluster.encrypted_password)))
            return cno_box[0]

        if run.cluster_role_added:
            def _role() -> str:
                _, note = get_cno().call(lambda s, sess: s.remove_cluster_vm_role(sess, run.vm_name))
                run.cluster_role_added = False
                return f"Cluster-Rolle entfernt{note}"

            step("rb-cluster-role", "Zurückrollen: Cluster-Rolle entfernen", _role)

        if run.vm_created and not run.cluster_role_added:
            def _vm() -> str:
                # Nur den vom Lauf selbst angelegten Ordner <Ablage>\<VM-Name> loeschen.
                expected = f"{run.storage_root}\\{run.vm_name}"
                if run.vm_folder != expected or not run.vm_name.strip() or run.vm_folder.rstrip("\\").lower() == run.storage_root.rstrip("\\").lower():
                    raise RuntimeError(f"Unerwarteter VM-Ordner {run.vm_folder} -- wird nicht gelöscht")
                cno = get_cno()
                node, session = _node_session(
                    cluster, cno.password, cno, cno.service.node_address_map(cno.session), run.node_name, credssp=_uses_unc(run),
                )
                node.remove_vm_and_folder(session, run.vm_name, run.vm_folder)
                run.vm_created = False
                return f"VM entfernt, Ordner {run.vm_folder} gelöscht"

            step("rb-vm", "Zurückrollen: VM und Ordner entfernen", _vm)

        if cluster is not None and not failures:
            try:
                _run_hyperv_discovery(db, cluster)
            except Exception:  # noqa: BLE001
                pass

        run = db.get(VmCreateRun, run_id)
        run.finished_at = datetime.now(timezone.utc)
        if failures or run.has_created_objects:
            run.status = RestoreStatus.FAILED
            run.error_message = ("Zurückrollen unvollständig: " + " | ".join(failures or ["Objekte übrig"]))[:2000]
            db.commit()
            _log(db, f"VM '{run.vm_name}' anlegen: {run.error_message}", level="ERROR")
        else:
            run.status = RestoreStatus.CLEANED_UP
            db.commit()
            _log(db, f"VM '{run.vm_name}' anlegen: vollständig zurückgerollt")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(VmCreateRun, run_id)
        if run is not None:
            run.status = RestoreStatus.FAILED
            run.error_message = f"Zurückrollen abgebrochen: {exc}"[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
