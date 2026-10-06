"""VM-Einstellungen aendern (Backlog #81): CPU, Arbeitsspeicher,
Netzwerkadapter und Festplatten einer vorhandenen VM.

  Vorpruefung -> CPU/Arbeitsspeicher -> Netzwerk -> Festplatten ->
  Cluster-Konfiguration -> Inventory

Was wann geht (Hyper-V-Regeln, im Dialog je Feld erklaert):
- CPU und Arbeitsspeicher: nur bei ausgeschalteter VM.
- Adapter umstecken (Switch/VLAN, trennen): jederzeit. Adapter hinzufuegen/
  entfernen: Generation 2 jederzeit, Generation 1 nur ausgeschaltet.
- Festplatte vergroessern: nie verkleinern; nicht, solange die VM Checkpoints
  hat oder die Disk eine Differenz-Disk ist; an einem IDE-Controller nur
  ausgeschaltet. Die Partition im Gast wird NICHT erweitert.
- Festplatte hinzufuegen: neue VHDX im Ordner der ersten Disk, am
  SCSI-Controller (laufend moeglich).

Gesperrt waehrend Backup, Restore, Verschiebung, Loeschung oder Power-Aktion
der VM. Es werden nur tatsaechliche Abweichungen vom Live-Stand ausgefuehrt
(_plan); die Vorpruefung im Lauf plant gegen den dann aktuellen Stand neu.
Kein Zurueckrollen: bricht ein Schritt ab, bleiben die bereits erledigten
Aenderungen bestehen (das Protokoll nennt sie).

VM auf SMB3: Disk-Schritte auf dem Knoten per CredSSP (Double-Hop)."""

import re
from datetime import datetime, timezone
from ntpath import basename as win_basename
from ntpath import dirname as win_dirname

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes import vm_delete
from app.api.routes.csv_create import _Cluster, _hyperv_service
from app.api.routes.hyperv_clusters import _apply_vm_discovery_refresh, _get_vm_settled
from app.api.routes.restore import _restore_settings, _StepCtx
from app.api.routes.vm_create import _node_session
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import SessionLocal, get_db
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVVm
from app.models.restore_run import RestoreStatus, RestoreStepStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_settings_run import VmSettingsRun, VmSettingsRunStep

router = APIRouter(prefix="/api/vm-settings", tags=["vm-settings"])

_manage = require_permission(Permission.HYPERV_MANAGE)

MB = 1024**2
GB = 1024**3
MAX_VHDX_BYTES = 64 * 1024**4
MIN_MEMORY_BYTES = 32 * MB


class AdapterInfo(BaseModel):
    id: str
    name: str
    switch_name: str | None = None
    mac_address: str | None = None
    vlan_id: int | None = None
    # Access | Untagged | Trunk ... -- alles ausser Access/Untagged wird nicht angefasst
    vlan_mode: str = "Untagged"


class DiskInfo(BaseModel):
    path: str
    name: str
    controller: str
    size_bytes: int | None = None
    file_size_bytes: int | None = None
    vhd_type: str | None = None
    expand_blocked_reason: str | None = None


class SwitchInfo(BaseModel):
    name: str
    type: str = ""


class VmSettingsInfo(BaseModel):
    cluster_id: str
    vm_name: str
    vm_id: str
    state: str
    generation: int
    node: str
    cpu_count: int
    host_logical_cpus: int
    memory_startup_bytes: int
    dynamic_memory_enabled: bool
    memory_minimum_bytes: int
    memory_maximum_bytes: int
    checkpoint_count: int
    adapters: list[AdapterInfo]
    switches: list[SwitchInfo]
    disks: list[DiskInfo]
    new_disk_folder: str | None = None
    # CPU/RAM aenderbar (VM aus) bzw. Adapter hinzufuegen/entfernen moeglich
    hardware_editable: bool
    adapters_addable: bool
    blocked_reasons: list[str]
    warnings: list[str]


class MemoryChange(BaseModel):
    startup_bytes: int
    dynamic: bool = False
    minimum_bytes: int | None = None
    maximum_bytes: int | None = None


class AdapterChange(BaseModel):
    id: str
    switch_name: str | None = None  # None = getrennt
    vlan_id: int | None = Field(default=None, ge=1, le=4094)  # None = ungetaggt


class NewAdapter(BaseModel):
    switch_name: str
    vlan_id: int | None = Field(default=None, ge=1, le=4094)


class DiskExpand(BaseModel):
    path: str
    size_bytes: int


class NewDisk(BaseModel):
    size_bytes: int
    dynamic: bool = True


class VmSettingsRequest(BaseModel):
    cluster_id: str
    vm_name: str
    cpu_count: int | None = Field(default=None, ge=1, le=2048)
    memory: MemoryChange | None = None
    adapters: list[AdapterChange] = []
    add_adapters: list[NewAdapter] = []
    remove_adapter_ids: list[str] = []
    expand_disks: list[DiskExpand] = []
    add_disks: list[NewDisk] = []


class StepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class VmSettingsRunRead(BaseModel):
    id: str
    vm_name: str
    node_name: str | None = None
    changes: list[str] = []
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[StepRead]

    class Config:
        from_attributes = True


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="vm-settings", message=message))
    db.commit()


def _gb(value: int | None) -> str:
    if value is None:
        return "?"
    return f"{value / GB:.1f}".replace(".", ",").removesuffix(",0") + " GB"


def _busy_reasons(db: Session, cluster_id: str, vm_name: str, own_run_id: str | None = None) -> list[str]:
    # vm_power._busy_reason meldet auch laufende Einstellungs-Laeufe (sperrt
    # Power/Loeschen/Verschieben) -- hier herausnehmen und selbst pruefen,
    # sonst blockiert sich der Lauf in seiner eigenen Vorpruefung.
    reasons = [r for r in vm_delete._busy_reasons(db, cluster_id, vm_name) if "Einstellungen geändert" not in r]
    if db.query(VmSettingsRun).filter(
        VmSettingsRun.hyperv_cluster_id == cluster_id, VmSettingsRun.vm_name == vm_name,
        VmSettingsRun.status == RestoreStatus.RUNNING, VmSettingsRun.id != (own_run_id or ""),
    ).first():
        reasons.append("Für diese VM läuft bereits eine Änderung der Einstellungen.")
    return reasons


def _expand_blocked(disk: dict, state: str, checkpoint_count: int) -> str | None:
    if disk.get("size_bytes") is None:
        return "Größe nicht lesbar"
    if checkpoint_count:
        return "VM hat Checkpoints -- erst löschen"
    if disk.get("parent_path") or disk.get("vhd_type") == "Differencing":
        return "Differenz-Disk"
    if not disk["path"].lower().endswith((".vhdx", ".vhd")):
        return "keine VHD/VHDX"
    if state != "Off" and disk.get("controller_type") == "IDE":
        return "IDE-Controller -- nur bei ausgeschalteter VM"
    return None


class _Live:
    """Live-Zugriff auf die VM: CNO, Knoten-Sitzung, Roh-Info."""

    def __init__(self, db: Session, cluster_id: str, vm_name: str, timeout: int = 120):
        self.cluster = db.get(HyperVCluster, cluster_id)
        vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
        if self.cluster is None or vm is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
        self.cno = _Cluster(self.cluster, decrypt_secret(self.cluster.encrypted_password))
        self.owner = self.cno.service.get_vm_owner_node(self.cno.session, vm_name) or vm.host_name
        if not self.owner:
            raise RuntimeError("Der Knoten der VM ist nicht bekannt -- bitte Discovery ausführen")
        self.node_ips = self.cno.service.node_address_map(self.cno.session)
        # Erst ohne CredSSP lesen; liegen Disks auf SMB3, fuer die Disk-Angaben per CredSSP nachlesen.
        self.node, self.session = _node_session(self.cluster, self.cno.password, self.cno, self.node_ips, self.owner, credssp=False, timeout=timeout)
        self.raw = self.node.vm_settings_info(self.session, vm_name)
        self.on_smb = any(d["path"].startswith("\\\\") for d in self.raw["disks"])
        self.disk_node, self.disk_session = self.node, self.session
        if self.on_smb:
            self.disk_node, self.disk_session = _node_session(
                self.cluster, self.cno.password, self.cno, self.node_ips, self.owner, credssp=True, timeout=timeout,
            )
            self.raw = self.disk_node.vm_settings_info(self.disk_session, vm_name)


    def open_write_sessions(self, timeout: int = 900) -> None:
        """Sitzungen fuer die AENDERNDEN Schritte. Jede Konfigurationsaenderung
        an einer geclusterten VM loest intern Update-ClusterVirtualMachine-
        Configuration aus -- ein zweiter Hop zum Cluster-Dienst, der ueber
        Kerberos scheitert (Cmdlet meldet Fehler, obwohl die Aenderung
        durchgefuehrt ist; live 2026-10-06 beim Anhaengen einer Disk an VM02).
        Deshalb wie beim Restore (_restore_settings) Kerberos -> NTLM; auf
        SMB3 fuer die Disk-Schritte weiterhin CredSSP."""
        service = _hyperv_service(self.cluster, self.node_ips.get(self.owner.lower(), self.owner), self.owner, settings=_restore_settings())
        self.node = service
        self.session = service.connect(self.cluster.username, self.cno.password, read_timeout_sec=timeout + 60, operation_timeout_sec=timeout)
        if not self.on_smb:
            self.disk_node, self.disk_session = self.node, self.session


def _to_info(db: Session, cluster_id: str, vm_name: str, live: _Live, own_run_id: str | None = None) -> VmSettingsInfo:
    raw = live.raw
    try:
        switches = live.node.list_vm_switches(live.session)
    except Exception:  # noqa: BLE001 -- nur Auswahl
        switches = []
    disks = [
        DiskInfo(
            path=d["path"], name=win_basename(d["path"]),
            controller=f"{d['controller_type']} {d['controller_number']}:{d['controller_location']}",
            size_bytes=d["size_bytes"], file_size_bytes=d["file_size_bytes"], vhd_type=d["vhd_type"],
            expand_blocked_reason=_expand_blocked(d, raw["state"], raw["checkpoint_count"]),
        )
        for d in raw["disks"]
    ]
    off = raw["state"] == "Off"
    warnings: list[str] = []
    if not off:
        warnings.append(f"Die VM ist nicht ausgeschaltet (Status {raw['state']}) -- CPU und Arbeitsspeicher lassen sich nur bei ausgeschalteter VM ändern.")
    if raw["checkpoint_count"]:
        warnings.append(f"Die VM hat {raw['checkpoint_count']} Checkpoint(s) -- Festplatten lassen sich erst nach dem Löschen der Checkpoints vergrößern.")
    special = [a["name"] for a in raw["adapters"] if a["vlan_mode"] not in ("Access", "Untagged")]
    if special:
        warnings.append("Adapter mit besonderem VLAN-Modus (Trunk o.ä.) -- deren VLAN-Einstellung wird hier nicht angezeigt und nicht geändert.")
    return VmSettingsInfo(
        cluster_id=cluster_id, vm_name=vm_name, vm_id=raw["vm_id"], state=raw["state"], generation=raw["generation"], node=live.owner,
        cpu_count=raw["cpu_count"], host_logical_cpus=raw["host_logical_cpus"], memory_startup_bytes=raw["memory_startup_bytes"],
        dynamic_memory_enabled=raw["dynamic_memory_enabled"], memory_minimum_bytes=raw["memory_minimum_bytes"],
        memory_maximum_bytes=raw["memory_maximum_bytes"], checkpoint_count=raw["checkpoint_count"],
        adapters=[AdapterInfo(**a) for a in raw["adapters"]], switches=[SwitchInfo(**s) for s in switches], disks=disks,
        new_disk_folder=win_dirname(raw["disks"][0]["path"]) if raw["disks"] else None,
        hardware_editable=off, adapters_addable=off or raw["generation"] == 2,
        blocked_reasons=_busy_reasons(db, cluster_id, vm_name, own_run_id), warnings=warnings,
    )


def load_info(db: Session, cluster_id: str, vm_name: str, own_run_id: str | None = None) -> tuple[VmSettingsInfo, _Live]:
    try:
        live = _Live(db, cluster_id, vm_name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc
    return _to_info(db, cluster_id, vm_name, live, own_run_id), live


def _new_disk_paths(info: VmSettingsInfo, count: int) -> list[str]:
    """Freie Dateinamen <VM>_diskN.vhdx im Ordner der ersten Disk."""
    taken = {d.name.lower() for d in info.disks}
    safe = re.sub(r'[\\/:*?"<>|]', "_", info.vm_name)
    paths, n = [], len(info.disks) + 1
    while len(paths) < count:
        name = f"{safe}_disk{n}.vhdx"
        if name.lower() not in taken:
            taken.add(name.lower())
            paths.append(f"{info.new_disk_folder}\\{name}")
        n += 1
    return paths


def _plan(info: VmSettingsInfo, payload: VmSettingsRequest) -> tuple[dict, list[str], list[str]]:
    """Vergleicht den Antrag mit dem Live-Stand. Liefert (effektive
    Aenderungen, lesbare Liste, Fehler). Nur echte Abweichungen."""
    plan: dict = {"cpu": None, "memory": None, "adapters": [], "add_adapters": [], "remove_adapters": [], "expand": [], "add_disks": []}
    changes: list[str] = []
    errors: list[str] = []
    switch_names = {s.name for s in info.switches}

    if payload.cpu_count is not None and payload.cpu_count != info.cpu_count:
        if not info.hardware_editable:
            errors.append("vCPU lassen sich nur bei ausgeschalteter VM ändern.")
        elif info.host_logical_cpus and payload.cpu_count > info.host_logical_cpus:
            errors.append(f"Der Knoten hat nur {info.host_logical_cpus} logische Prozessoren.")
        else:
            plan["cpu"] = payload.cpu_count
            changes.append(f"vCPU {info.cpu_count} → {payload.cpu_count}")

    m = payload.memory
    if m is not None:
        current = (info.memory_startup_bytes, info.dynamic_memory_enabled,
                   info.memory_minimum_bytes if info.dynamic_memory_enabled else None,
                   info.memory_maximum_bytes if info.dynamic_memory_enabled else None)
        wanted = (m.startup_bytes, m.dynamic, m.minimum_bytes if m.dynamic else None, m.maximum_bytes if m.dynamic else None)
        if wanted != current:
            if not info.hardware_editable:
                errors.append("Arbeitsspeicher lässt sich nur bei ausgeschalteter VM ändern.")
            elif m.startup_bytes < MIN_MEMORY_BYTES or m.startup_bytes % (2 * MB):
                errors.append("Arbeitsspeicher: mindestens 32 MB, in Schritten von 2 MB.")
            elif m.dynamic and not (m.minimum_bytes and m.maximum_bytes):
                errors.append("Dynamischer Arbeitsspeicher braucht Minimum und Maximum.")
            elif m.dynamic and not (MIN_MEMORY_BYTES <= m.minimum_bytes <= m.startup_bytes <= m.maximum_bytes):
                errors.append("Dynamischer Arbeitsspeicher: Minimum ≤ Start ≤ Maximum.")
            elif m.dynamic and (m.minimum_bytes % (2 * MB) or m.maximum_bytes % (2 * MB)):
                errors.append("Arbeitsspeicher-Minimum und -Maximum in Schritten von 2 MB.")
            else:
                plan["memory"] = m.model_dump()
                text = f"Arbeitsspeicher {_gb(info.memory_startup_bytes)} → {_gb(m.startup_bytes)}"
                text += f", dynamisch {_gb(m.minimum_bytes)}–{_gb(m.maximum_bytes)}" if m.dynamic else ", statisch"
                changes.append(text)

    by_id = {a.id: a for a in info.adapters}
    removing = set(payload.remove_adapter_ids)
    for change in payload.adapters:
        adapter = by_id.get(change.id)
        if adapter is None or change.id in removing:
            if adapter is None:
                errors.append("Ein Netzwerkadapter existiert nicht mehr -- bitte Dialog neu öffnen.")
            continue
        if adapter.vlan_mode not in ("Access", "Untagged"):
            vlan_same = True  # besonderen VLAN-Modus nicht anfassen
        else:
            vlan_same = change.vlan_id == adapter.vlan_id
        if change.switch_name == adapter.switch_name and vlan_same:
            continue
        if adapter.vlan_mode not in ("Access", "Untagged"):
            errors.append(f"Adapter {adapter.mac_address or adapter.name}: besonderer VLAN-Modus ({adapter.vlan_mode}), hier nicht änderbar.")
            continue
        if change.switch_name and switch_names and change.switch_name not in switch_names:
            errors.append(f"Switch '{change.switch_name}' gibt es auf {info.node} nicht.")
            continue
        plan["adapters"].append(change.model_dump())
        changes.append(
            f"Adapter {adapter.mac_address or adapter.name}: {adapter.switch_name or 'getrennt'}"
            f"{f' VLAN {adapter.vlan_id}' if adapter.vlan_id else ''} → {change.switch_name or 'getrennt'}"
            f"{f' VLAN {change.vlan_id}' if change.vlan_id else ''}"
        )
    if (removing or payload.add_adapters) and not info.adapters_addable:
        errors.append("Adapter hinzufügen/entfernen geht bei Generation 1 nur bei ausgeschalteter VM.")
    else:
        for adapter_id in payload.remove_adapter_ids:
            adapter = by_id.get(adapter_id)
            if adapter is None:
                errors.append("Ein Netzwerkadapter existiert nicht mehr -- bitte Dialog neu öffnen.")
                continue
            plan["remove_adapters"].append(adapter_id)
            changes.append(f"Adapter {adapter.mac_address or adapter.name} ({adapter.switch_name or 'getrennt'}) entfernen")
        for new in payload.add_adapters:
            if switch_names and new.switch_name not in switch_names:
                errors.append(f"Switch '{new.switch_name}' gibt es auf {info.node} nicht.")
                continue
            plan["add_adapters"].append(new.model_dump())
            changes.append(f"Neuer Adapter an {new.switch_name}{f' VLAN {new.vlan_id}' if new.vlan_id else ''}")

    disks = {d.path.lower(): d for d in info.disks}
    for expand in payload.expand_disks:
        disk = disks.get(expand.path.lower())
        if disk is None:
            errors.append(f"Festplatte {win_basename(expand.path)} hängt nicht mehr an der VM.")
        elif disk.size_bytes is not None and expand.size_bytes == disk.size_bytes:
            continue
        elif disk.expand_blocked_reason:
            errors.append(f"{disk.name}: {disk.expand_blocked_reason}.")
        elif expand.size_bytes < disk.size_bytes:
            errors.append(f"{disk.name}: Verkleinern ist nicht möglich.")
        elif expand.size_bytes > MAX_VHDX_BYTES or expand.size_bytes % MB:
            errors.append(f"{disk.name}: höchstens 64 TB, in ganzen MB.")
        else:
            plan["expand"].append({"path": disk.path, "size_bytes": expand.size_bytes})
            changes.append(f"{disk.name} vergrößern {_gb(disk.size_bytes)} → {_gb(expand.size_bytes)}")
    if payload.add_disks:
        if not info.new_disk_folder:
            errors.append("Neue Festplatte: Die VM hat noch keine Disk, deren Ordner als Ablage dienen könnte.")
        else:
            for new, path in zip(payload.add_disks, _new_disk_paths(info, len(payload.add_disks))):
                if not (GB <= new.size_bytes <= MAX_VHDX_BYTES) or new.size_bytes % MB:
                    errors.append("Neue Festplatte: 1 GB bis 64 TB, in ganzen MB.")
                    continue
                plan["add_disks"].append({"path": path, "size_bytes": new.size_bytes, "dynamic": new.dynamic})
                changes.append(f"Neue Festplatte {win_basename(path)} ({_gb(new.size_bytes)}, {'dynamisch' if new.dynamic else 'fest'})")
    return plan, changes, errors


@router.get("/runs/{run_id}", response_model=VmSettingsRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmSettingsRun:
    run = db.get(VmSettingsRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.get("/{cluster_id}/{vm_name}", response_model=VmSettingsInfo)
def get_info(cluster_id: str, vm_name: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmSettingsInfo:
    return load_info(db, cluster_id, vm_name)[0]


@router.post("", response_model=VmSettingsRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_change(
    payload: VmSettingsRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(_manage),
) -> VmSettingsRun:
    info, _ = load_info(db, payload.cluster_id, payload.vm_name)
    if info.blocked_reasons:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(info.blocked_reasons))
    _, changes, errors = _plan(info, payload)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine Änderung gegenüber dem aktuellen Stand.")
    run = VmSettingsRun(
        hyperv_cluster_id=payload.cluster_id, vm_name=payload.vm_name, vm_uuid=info.vm_id or None, node_name=info.node,
        request=payload.model_dump(), changes=changes, requested_by=user.display_name or user.username,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_change, run.id)
    return run


def _each(items: list, action, describe) -> str:
    """Fuehrt action je Eintrag aus; bei einem Fehler nennt die Meldung, was
    davor schon erledigt war (kein Zurueckrollen)."""
    done: list[str] = []
    for item in items:
        try:
            action(item)
        except Exception as exc:
            prefix = f"bereits erledigt: {'; '.join(done)} -- " if done else ""
            raise RuntimeError(f"{prefix}{describe(item)} fehlgeschlagen: {exc}") from exc
        done.append(describe(item))
    return "; ".join(done)


def _refresh_inventory(db: Session, run: VmSettingsRun, live: _Live) -> str:
    refreshed = _get_vm_settled(
        live.disk_node, live.disk_session, run.vm_name, attempts=1, username=live.cluster.username, password=live.cno.password,
    )
    if refreshed is None:
        raise RuntimeError("VM auf dem Knoten nicht gefunden")
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name).first()
    if vm is None:
        raise RuntimeError("VM steht nicht (mehr) im Inventory")
    vm.state = refreshed.state
    vm.cpu_count = refreshed.cpu_count
    vm.memory_startup_bytes = refreshed.memory_startup_bytes
    vm.memory_minimum_bytes = refreshed.memory_minimum_bytes
    vm.memory_maximum_bytes = refreshed.memory_maximum_bytes
    vm.dynamic_memory_enabled = refreshed.dynamic_memory_enabled
    vm.network_adapters = [
        {"name": n.name, "mac_address": n.mac_address, "switch_name": n.switch_name, "vlan_id": n.vlan_id} for n in refreshed.network_adapters
    ]
    _apply_vm_discovery_refresh(db, run.hyperv_cluster_id, vm, refreshed)
    db.commit()
    return f"{refreshed.cpu_count} vCPU, {_gb(refreshed.memory_startup_bytes)} RAM, {len(refreshed.network_adapters)} Adapter, {len(refreshed.vhds)} Disk(s)"


def _execute_change(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    live: _Live | None = None
    touched = False
    try:
        run = db.get(VmSettingsRun, run_id)
        if run is None:
            return
        name = run.vm_name
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=VmSettingsRunStep) as ctx:
                live = _Live(db, run.hyperv_cluster_id, name, timeout=900)
                info = _to_info(db, run.hyperv_cluster_id, name, live, own_run_id=run.id)
                if info.blocked_reasons:
                    raise RuntimeError(" ".join(info.blocked_reasons))
                if run.vm_uuid and info.vm_id and info.vm_id.lower() != run.vm_uuid.lower():
                    raise RuntimeError("Unter diesem Namen läuft inzwischen eine andere VM -- bitte neu öffnen")
                plan, changes, errors = _plan(info, VmSettingsRequest(**run.request))
                if errors:
                    raise RuntimeError(" ".join(errors))
                if not changes:
                    raise RuntimeError("Keine Änderung mehr gegenüber dem aktuellen Stand")
                live.open_write_sessions()
                run.node_name = info.node
                run.changes = changes
                ctx.row.message = f"{info.node}, Status {info.state}, {len(changes)} Änderung(en)" + (" (SMB3: CredSSP)" if live.on_smb else "")

            if plan["cpu"] is not None or plan["memory"] is not None:
                with _StepCtx(db, run.id, "hardware", "CPU / Arbeitsspeicher", step_model=VmSettingsRunStep) as ctx:
                    memory = plan["memory"] or {}
                    touched = True
                    live.node.configure_vm_hardware(
                        live.session, name, plan["cpu"], memory.get("startup_bytes"), memory.get("minimum_bytes"),
                        memory.get("maximum_bytes"), memory.get("dynamic"),
                    )
                    ctx.row.message = "; ".join(c for c in changes if c.startswith(("vCPU", "Arbeitsspeicher")))

            if plan["adapters"] or plan["remove_adapters"] or plan["add_adapters"]:
                with _StepCtx(db, run.id, "network", "Netzwerk", step_model=VmSettingsRunStep) as ctx:
                    touched = True
                    parts = [
                        _each(
                            plan["adapters"],
                            lambda a: live.node.set_network_adapter(live.session, name, a["id"], a["switch_name"], a["vlan_id"]),
                            lambda a: f"Adapter → {a['switch_name'] or 'getrennt'}{' VLAN ' + str(a['vlan_id']) if a['vlan_id'] else ''}",
                        ),
                        _each(
                            plan["remove_adapters"], lambda i: live.node.remove_network_adapter(live.session, name, i),
                            lambda i: "Adapter entfernt",
                        ),
                        _each(
                            plan["add_adapters"],
                            lambda a: live.node.add_network_adapter(live.session, name, a["switch_name"], a["vlan_id"]),
                            lambda a: f"neuer Adapter an {a['switch_name']}{' VLAN ' + str(a['vlan_id']) if a['vlan_id'] else ''}",
                        ),
                    ]
                    ctx.row.message = "; ".join(p for p in parts if p)

            if plan["expand"] or plan["add_disks"]:
                with _StepCtx(db, run.id, "disks", "Festplatten", step_model=VmSettingsRunStep) as ctx:
                    touched = True
                    notes: list[str] = []

                    def _add_disk(d: dict) -> None:
                        note = live.disk_node.add_vm_disk(live.disk_session, name, d["path"], d["size_bytes"], d["dynamic"])
                        if note:
                            notes.append(f"{win_basename(d['path'])} ist angehängt, Hyper-V meldete dabei: {note}")

                    parts = [
                        _each(
                            plan["expand"], lambda d: live.disk_node.resize_vhd(live.disk_session, d["path"], d["size_bytes"]),
                            lambda d: f"{win_basename(d['path'])} auf {_gb(d['size_bytes'])}",
                        ),
                        _each(plan["add_disks"], _add_disk, lambda d: f"neu {win_basename(d['path'])} ({_gb(d['size_bytes'])})"),
                    ]
                    ctx.row.message = "; ".join(p for p in [*parts, *notes] if p)
                    if plan["expand"]:
                        ctx.row.message += " -- die Partition im Gast bitte dort erweitern"

            if live.raw["is_clustered"]:
                with _StepCtx(db, run.id, "cluster", "Cluster-Konfiguration aktualisieren", step_model=VmSettingsRunStep) as ctx:
                    # bewusst die normale Sitzung (nicht CredSSP), siehe update_cluster_vm_configuration
                    result = live.node.update_cluster_vm_configuration(live.session, name)
                    if not result.success:
                        raise RuntimeError(result.error)
                    ctx.row.message = "abgeglichen"

            warning = None
            with _StepCtx(db, run.id, "inventory", "Inventory aktualisieren", step_model=VmSettingsRunStep) as ctx:
                try:
                    ctx.row.message = _refresh_inventory(db, run, live)
                except Exception as exc:  # noqa: BLE001 -- die Aenderung selbst ist durch
                    db.rollback()
                    warning = str(exc)[:300]
                    ctx.row.message = f"nicht aktualisiert ({warning}) -- zieht mit der nächsten Discovery nach"
            if warning:
                step = db.query(VmSettingsRunStep).filter(VmSettingsRunStep.run_id == run_id, VmSettingsRunStep.step == "inventory").first()
                if step is not None:
                    step.status = RestoreStepStatus.ERROR

            run = db.get(VmSettingsRun, run_id)
            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"VM '{name}' Einstellungen geändert auf {run.node_name}: {'; '.join(run.changes)} (durch {run.requested_by})")
        except Exception as exc:
            db.rollback()
            run = db.get(VmSettingsRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            run.error_message = (f"{detail}" + (" -- bereits ausgeführte Änderungen bleiben bestehen, siehe Protokoll" if touched else ""))[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"VM '{run.vm_name}' Einstellungen ändern fehlgeschlagen: {run.error_message} (durch {run.requested_by})", level="ERROR")
            if touched and live is not None:
                # Teilweise geaendert: Cluster-Konfiguration und Inventory trotzdem nachziehen.
                try:
                    if live.raw["is_clustered"]:
                        live.node.update_cluster_vm_configuration(live.session, run.vm_name)
                    _refresh_inventory(db, run, live)
                except Exception:  # noqa: BLE001
                    db.rollback()
    finally:
        db.close()
