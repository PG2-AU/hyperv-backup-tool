"""VM loeschen (Nutzer-Anfrage 2026-10-02), Gegenstueck zu
app.api.routes.vm_create und nach denselben Regeln wie CSV/SMB3 loeschen:

  Vorpruefung -> Cluster-Rolle entfernen -> VM entfernen -> aus Protection
  Groups austragen -> Dateien loeschen (Opt-out) -> Inventory

- Gesperrt waehrend Backup, Restore, Verschiebung oder Power-Aktion der VM.
- Eine nicht ausgeschaltete VM nur mit ausdruecklichem "vorher hart
  ausschalten".
- Dateien (Opt-out): alle Disk-Dateien der VM inkl. Checkpoint-Ketten, VOR
  dem Entfernen live ermittelt. Eine Disk, die laut Inventory auch an einer
  anderen VM haengt, bleibt stehen. Danach werden nur LEER gewordene Ordner
  entfernt (Virtual Hard Disks, Snapshots, Virtual Machines und der VM-Ordner
  selbst, sofern er wie die VM heisst) -- nie rekursiv.
- Die Backups der VM bleiben erhalten (Wiederherstellung per "VM neu
  erstellen" weiter moeglich).
- Bestaetigung durch Eintippen des VM-Namens. Kein Zurueckrollen.

VM auf SMB3: die Schritte auf dem Knoten laufen per CredSSP (Double-Hop)."""

from datetime import datetime, timezone
from ntpath import basename as win_basename
from ntpath import dirname as win_dirname

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes import vm_power
from app.api.routes.csv_create import _Cluster
from app.api.routes.hyperv_clusters import _run_discovery as _run_hyperv_discovery
from app.api.routes.restore import _StepCtx
from app.api.routes.vm_create import _node_session
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.storage_move import split_storage_path
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRunVmConfig
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVVhd, HyperVVm
from app.models.resource_group import ResourceGroup, parse_member_key
from app.models.backup_policy import BackupScope
from app.models.restore_run import RestoreStatus, RestoreStepStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_delete_run import VmDeleteRun, VmDeleteRunStep

router = APIRouter(prefix="/api/vm-delete", tags=["vm-delete"])

_manage = require_permission(Permission.HYPERV_MANAGE)
# Von Hyper-V unterhalb des VM-Ordners angelegte Unterordner.
_VM_SUBFOLDERS = ("Virtual Machines", "Snapshots", "Virtual Hard Disks", "Planned Virtual Machines", "UndoLog Configuration")


class FileEntry(BaseModel):
    path: str
    size_bytes: int | None = None
    # Gesetzt = bleibt stehen (wird von dieser VM mitbenutzt).
    shared_with: str | None = None


class VmDeleteInfo(BaseModel):
    cluster_id: str
    vm_name: str
    vm_id: str
    state: str
    node: str
    configuration_location: str | None = None
    checkpoint_count: int
    files: list[FileEntry]
    folders: list[str]
    total_bytes: int
    backup_count: int
    protection_groups: list[str]
    blocked_reasons: list[str]
    warnings: list[str]


class VmDeleteRequest(BaseModel):
    cluster_id: str
    vm_name: str
    confirm_name: str
    delete_files: bool = True
    turn_off: bool = False


class StepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class VmDeleteRunRead(BaseModel):
    id: str
    vm_name: str
    node_name: str | None = None
    delete_files: bool
    turn_off: bool
    removed_from_groups: list[str] = []
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[StepRead]

    class Config:
        from_attributes = True


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="vm-delete", message=message))
    db.commit()


def _is_member(member: str, cluster_id: str, vm_name: str) -> bool:
    member_cluster, name = parse_member_key(member)
    return member_cluster in (cluster_id, None) and name == vm_name


def _groups_with_vm(db: Session, cluster_id: str, vm_name: str) -> list[ResourceGroup]:
    return [
        g for g in db.query(ResourceGroup).filter(ResourceGroup.scope == BackupScope.VM).all()
        if any(_is_member(m, cluster_id, vm_name) for m in (g.members or []))
    ]


def _busy_reasons(db: Session, cluster_id: str, vm_name: str, own_run_id: str | None = None) -> list[str]:
    reasons = []
    busy = vm_power._busy_reason(db, cluster_id, vm_name)
    if busy:
        reasons.append(busy)
    with vm_power._lock:
        action = vm_power._actions.get((cluster_id, vm_name))
    if action is not None and action.status == "running":
        reasons.append("Für diese VM läuft gerade eine Power-Aktion.")
    if db.query(VmDeleteRun).filter(
        VmDeleteRun.hyperv_cluster_id == cluster_id, VmDeleteRun.vm_name == vm_name,
        VmDeleteRun.status == RestoreStatus.RUNNING, VmDeleteRun.id != (own_run_id or ""),
    ).first():
        reasons.append("Diese VM wird bereits gelöscht.")
    return reasons


def _candidate_folders(vm_name: str, info: dict, file_paths: list[str]) -> list[str]:
    """Ordner, die nach dem Loeschen entfernt werden, WENN sie leer sind --
    tiefste zuerst. Nur der eigene VM-Ordner (heisst wie die VM, nicht die
    Wurzel der Ablage) und die Hyper-V-Unterordner DARIN. Liegt die VM direkt
    im Stamm einer CSV/Freigabe (z.B. C:\\ClusterStorage\\CSV01\\Virtual Hard
    Disks, live gesehen bei win10client01), wird kein Ordner angefasst --
    die teilen sich dort mehrere VMs."""
    bases = {win_dirname(p) for p in file_paths}
    for location in (info.get("configuration_location"), info.get("snapshot_file_location"), info.get("smart_paging_file_path")):
        if location:
            bases.add(location.rstrip("\\"))
    vm_folders: set[str] = set()
    for base in bases:
        for folder in (base, win_dirname(base)):
            parts = split_storage_path(folder)
            if parts is not None and parts[1].strip("\\") and win_basename(folder).lower() == vm_name.lower():
                vm_folders.add(folder)
    folders: set[str] = set(vm_folders)
    for vm_folder in vm_folders:
        folders.update(f"{vm_folder}\\{sub}" for sub in _VM_SUBFOLDERS)
    return sorted(folders, key=lambda f: (-f.count("\\"), f.lower()))


def load_info(db: Session, cluster_id: str, vm_name: str, own_run_id: str | None = None) -> VmDeleteInfo:
    cluster = db.get(HyperVCluster, cluster_id)
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if cluster is None or vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
    vhd_rows = db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id).all()
    on_smb = any(v.vm_name == vm_name and (v.path or "").startswith("\\\\") for v in vhd_rows)
    try:
        cno = _Cluster(cluster, decrypt_secret(cluster.encrypted_password))
        owner = cno.service.get_vm_owner_node(cno.session, vm_name) or vm.host_name
        if not owner:
            raise RuntimeError("Der Knoten der VM ist nicht bekannt -- bitte Discovery ausführen")
        node, session = _node_session(
            cluster, cno.password, cno, cno.service.node_address_map(cno.session), owner, credssp=on_smb, timeout=120,
        )
        info = node.vm_delete_info(session, vm_name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc

    # Disks, die laut Inventory auch an einer ANDEREN VM haengen, bleiben stehen.
    other_users: dict[str, str] = {}
    for row in vhd_rows:
        if row.path and row.vm_name and row.vm_name != vm_name:
            other_users.setdefault(row.path.lower(), row.vm_name)
    files = [
        FileEntry(path=f["path"], size_bytes=f["size_bytes"], shared_with=other_users.get(f["path"].lower()))
        for f in info["files"]
    ]
    deletable = [f.path for f in files if not f.shared_with]

    blocked = _busy_reasons(db, cluster_id, vm_name, own_run_id)
    warnings: list[str] = []
    if info["state"] != "Off":
        warnings.append(f"Die VM ist nicht ausgeschaltet (Status {info['state']}) -- Löschen nur mit „vorher hart ausschalten“.")
    if info["checkpoint_count"]:
        warnings.append(f"Die VM hat {info['checkpoint_count']} Checkpoint(s) -- sie werden mit entfernt, ihre Disk-Dateien mit gelöscht.")
    shared = [f for f in files if f.shared_with]
    if shared:
        warnings.append(
            "Bleibt stehen, weil eine andere VM die Disk mitbenutzt: "
            + ", ".join(f"{win_basename(f.path)} ({f.shared_with})" for f in shared)
        )
    missing = [f for f in files if f.size_bytes is None]
    if missing:
        warnings.append(f"{len(missing)} Disk-Datei(en) sind vom Knoten aus nicht lesbar: " + ", ".join(win_basename(f.path) for f in missing))
    groups = [g.name for g in _groups_with_vm(db, cluster_id, vm_name)]
    if groups:
        warnings.append(f"Die VM wird aus den Protection Groups {', '.join(groups)} ausgetragen.")
    backup_count = db.query(BackupRunVmConfig).filter(
        BackupRunVmConfig.hyperv_cluster_id == cluster_id, BackupRunVmConfig.vm_name == vm_name,
        BackupRunVmConfig.not_captured.is_(False),
    ).count()

    return VmDeleteInfo(
        cluster_id=cluster_id, vm_name=vm_name, vm_id=info["vm_id"], state=info["state"], node=owner,
        configuration_location=info["configuration_location"], checkpoint_count=info["checkpoint_count"], files=files,
        folders=_candidate_folders(vm_name, info, deletable), total_bytes=sum(f.size_bytes or 0 for f in files if not f.shared_with),
        backup_count=backup_count, protection_groups=groups, blocked_reasons=blocked, warnings=warnings,
    )


@router.get("/runs/{run_id}", response_model=VmDeleteRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmDeleteRun:
    run = db.get(VmDeleteRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.get("/{cluster_id}/{vm_name}", response_model=VmDeleteInfo)
def get_info(cluster_id: str, vm_name: str, db: Session = Depends(get_db), user=Depends(_manage)) -> VmDeleteInfo:
    return load_info(db, cluster_id, vm_name)


@router.post("", response_model=VmDeleteRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_delete(
    payload: VmDeleteRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(_manage),
) -> VmDeleteRun:
    if payload.confirm_name != payload.vm_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zur Bestätigung den VM-Namen exakt eintippen.")
    info = load_info(db, payload.cluster_id, payload.vm_name)
    if info.blocked_reasons:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(info.blocked_reasons))
    if info.state != "Off" and not payload.turn_off:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Die VM ist nicht ausgeschaltet (Status {info.state}) -- zuerst herunterfahren oder „vorher hart ausschalten“ wählen.",
        )
    run = VmDeleteRun(
        hyperv_cluster_id=payload.cluster_id, vm_name=payload.vm_name, vm_uuid=info.vm_id or None, node_name=info.node,
        delete_files=payload.delete_files, turn_off=payload.turn_off,
        files=[f.path for f in info.files if not f.shared_with] if payload.delete_files else [],
        folders=info.folders if payload.delete_files else [], removed_from_groups=[],
        requested_by=user.display_name or user.username, status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_delete, run.id)
    return run


def _execute_delete(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    vm_removed = False
    try:
        run = db.get(VmDeleteRun, run_id)
        if run is None:
            return
        name = run.vm_name
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=VmDeleteRunStep) as ctx:
                info = load_info(db, run.hyperv_cluster_id, name, own_run_id=run.id)
                if info.blocked_reasons:
                    raise RuntimeError(" ".join(info.blocked_reasons))
                if info.state != "Off" and not run.turn_off:
                    raise RuntimeError(f"Die VM ist nicht ausgeschaltet (Status {info.state})")
                if run.vm_uuid and info.vm_id and info.vm_id.lower() != run.vm_uuid.lower():
                    raise RuntimeError("Unter diesem Namen läuft inzwischen eine andere VM -- bitte neu öffnen")
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                cno = _Cluster(cluster, decrypt_secret(cluster.encrypted_password))
                on_smb = any(p.startswith("\\\\") for p in [*run.files, *run.folders, info.configuration_location or ""])
                node, session = _node_session(
                    cluster, cno.password, cno, cno.service.node_address_map(cno.session), info.node, credssp=on_smb, timeout=600,
                )
                run.node_name = info.node
                # Dateien frisch festhalten -- nach Remove-VM nicht mehr abfragbar.
                if run.delete_files:
                    run.files = [f.path for f in info.files if not f.shared_with]
                    run.folders = info.folders
                ctx.row.message = f"{info.node}, Status {info.state}, {len(run.files)} Datei(en)" + (" (SMB3: CredSSP)" if on_smb else "")

            with _StepCtx(db, run.id, "cluster-role", "Cluster-Rolle entfernen", step_model=VmDeleteRunStep) as ctx:
                _, note = cno.call(lambda s, sess: s.remove_cluster_vm_role(sess, name))
                ctx.row.message = f"aus dem Cluster entfernt{note}"

            with _StepCtx(db, run.id, "vm", "VM entfernen", step_model=VmDeleteRunStep) as ctx:
                node.remove_vm(session, name, run.turn_off)
                vm_removed = True
                ctx.row.message = "aus Hyper-V entfernt" + (" (vorher hart ausgeschaltet)" if run.turn_off and info.state != "Off" else "")

            with _StepCtx(db, run.id, "protection-groups", "Aus Protection Groups austragen", step_model=VmDeleteRunStep) as ctx:
                names = []
                for group in _groups_with_vm(db, run.hyperv_cluster_id, name):
                    group.members = [m for m in (group.members or []) if not _is_member(m, run.hyperv_cluster_id, name)]
                    names.append(group.name)
                run.removed_from_groups = names
                ctx.row.message = ", ".join(names) if names else "in keiner Protection Group"

            warnings: list[str] = []
            if run.delete_files:
                with _StepCtx(db, run.id, "files", "Dateien löschen", step_model=VmDeleteRunStep) as ctx:
                    result = node.delete_vm_files(session, list(run.files), list(run.folders))
                    text = f"{len(result['deleted'])} Datei(en), {len(result['removed'])} Ordner gelöscht"
                    if result["kept"]:
                        text += f"; nicht leer, bleibt stehen: {', '.join(result['kept'])}"
                    ctx.row.message = text
                    if result["failed"]:
                        raise RuntimeError(f"{text}; nicht löschbar: {' | '.join(result['failed'])}")

            with _StepCtx(db, run.id, "inventory", "Inventory aktualisieren", step_model=VmDeleteRunStep) as ctx:
                try:
                    _run_hyperv_discovery(db, cluster)
                    ctx.row.message = "Discovery abgeschlossen"
                except Exception as exc:  # noqa: BLE001
                    # Discovery nicht moeglich (z.B. Backup laeuft): Zeilen direkt entfernen.
                    db.rollback()
                    db.query(HyperVVhd).filter(HyperVVhd.cluster_id == run.hyperv_cluster_id, HyperVVhd.vm_name == name).delete()
                    db.query(HyperVVm).filter(HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == name).delete()
                    db.commit()
                    warnings.append(f"Discovery fehlgeschlagen: {exc}")
                    ctx.row.message = f"VM aus dem Inventory entfernt (Discovery fehlgeschlagen: {str(exc)[:300]})"
            if warnings:
                step = db.query(VmDeleteRunStep).filter(VmDeleteRunStep.run_id == run_id, VmDeleteRunStep.step == "inventory").first()
                if step is not None:
                    step.status = RestoreStepStatus.ERROR

            run = db.get(VmDeleteRun, run_id)
            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"VM '{name}' gelöscht auf {run.node_name}"
                + (f", {len(run.files)} Datei(en) entfernt" if run.delete_files else ", Dateien behalten")
                + (f", ausgetragen aus {', '.join(run.removed_from_groups)}" if run.removed_from_groups else "")
                + f" (durch {run.requested_by})",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(VmDeleteRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            left = " -- die VM ist bereits aus Hyper-V entfernt, übrige Dateien bitte von Hand prüfen" if vm_removed else ""
            run.error_message = f"{detail}{left}"[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"VM '{run.vm_name}' löschen fehlgeschlagen: {run.error_message} (durch {run.requested_by})", level="ERROR")
            if vm_removed:
                # Inventory trotzdem nachziehen, sonst steht die entfernte VM weiter in der Liste.
                try:
                    db.query(HyperVVhd).filter(HyperVVhd.cluster_id == run.hyperv_cluster_id, HyperVVhd.vm_name == run.vm_name).delete()
                    db.query(HyperVVm).filter(HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name).delete()
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
    finally:
        db.close()
