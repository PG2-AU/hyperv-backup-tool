"""VM-Restore: klont eine LUN aus einem Backup-Snapshot, meldet sich per
iSCSI direkt vom Container aus bei der Ziel-SVM an, kopiert die
wiederhergestellte VHDX per SMB auf die Ziel-CSV, und haengt sie an die VM
an (neue Zusatzdisk, mode='add') oder ersetzt die laufende VHDX damit
(mode='replace' -- VM wird dafuer kurz gestoppt, die alte Datei wird
geloescht, nicht nur umbenannt, siehe Chat-Verlauf).

Laeuft als Hintergrund-Task (FastAPI BackgroundTasks) mit eigener DB-Session:
ein Lauf (LUN-Klon + iSCSI + Kopie potenziell grosser VHDX-Dateien) kann
laenger dauern als ein synchroner HTTP-Request/nginx-Timeout sinnvoll
zulaesst. Fortschritt wird laufend in RestoreRun/RestoreRunStep persistiert;
das Frontend pollt GET /runs/{id} fuer die Live-Anzeige (analog zur
'Laufende Backup-Jobs'-Anzeige, aber mit sichtbaren Einzelschritten).

Bei 'add' bleibt die neue VHDX als Zusatzdisk an der VM haengen, bis der
Nutzer den Cleanup explizit ueber POST /runs/{id}/cleanup ausloest (Disk
abhaengen + Datei loeschen) -- siehe RestoreRun.cleanup_needed."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRunSnapshot
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVm
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster
from app.models.netapp_discovery import NetAppLun
from app.models.restore_infra import RestoreInfraConfig
from app.models.restore_run import RestoreMode, RestoreRun, RestoreRunStep, RestoreStatus, RestoreStepStatus
from app.services import restore_execution_service as exec_svc
from app.services.hyperv_service import HyperVService
from app.services.netapp_service import NetAppConnectionError, NetAppOntapService

router = APIRouter(prefix="/api/restore", tags=["restore"])

_CSV_NAME_RE = re.compile(r"ClusterStorage\\([^\\]+)\\", re.IGNORECASE)


def _parse_csv_name(vhd_path: str) -> str | None:
    match = _CSV_NAME_RE.search(vhd_path)
    return match.group(1) if match else None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "vm"


class VmWithBackupsRead(BaseModel):
    name: str
    host: str | None = None
    state: str | None = None
    cluster: str | None = None
    backup_count: int


class RestoreRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class RestoreRunRead(BaseModel):
    id: str
    vm_name: str
    mode: str
    status: str
    source_vhd_path: str
    restored_vhd_path: str | None = None
    cleanup_needed: bool
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[RestoreRunStepRead]

    class Config:
        from_attributes = True


class TriggerRestoreRequest(BaseModel):
    vm_name: str
    snapshot_id: str
    source_vhd_path: str
    mode: RestoreMode


@router.get("/vms", response_model=list[VmWithBackupsRead])
def list_vms_with_backups(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> list[VmWithBackupsRead]:
    counts: dict[str, int] = {}
    for snap in db.query(BackupRunSnapshot).filter(BackupRunSnapshot.success.is_(True)).all():
        for vm_name in snap.vm_names or []:
            counts[vm_name] = counts.get(vm_name, 0) + 1
    if not counts:
        return []
    vms = db.query(HyperVVm).filter(HyperVVm.name.in_(counts.keys())).all()
    cluster_names = {c.id: c.name for c in db.query(HyperVCluster).all()}
    return [
        VmWithBackupsRead(
            name=vm.name, host=vm.host_name, state=vm.state,
            cluster=cluster_names.get(vm.cluster_id), backup_count=counts.get(vm.name, 0),
        )
        for vm in vms
    ]


@router.get("/runs", response_model=list[RestoreRunRead])
def list_runs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN))) -> list[RestoreRun]:
    return db.query(RestoreRun).order_by(RestoreRun.started_at.desc()).all()


@router.get("/runs/{run_id}", response_model=RestoreRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN))) -> RestoreRun:
    run = db.get(RestoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restore-Lauf nicht gefunden")
    return run


class _StepCtx:
    """Persistiert Start/Ende eines Restore-Schritts live in die DB, damit
    das Frontend per Polling den Fortschritt sieht, waehrend der
    Hintergrund-Task noch laeuft."""

    def __init__(self, db: Session, run_id: str, step_id: str, label: str):
        self.db = db
        self.row = RestoreRunStep(run_id=run_id, step=step_id, label=label, status=RestoreStepStatus.RUNNING)
        db.add(self.row)
        db.commit()

    def __enter__(self) -> "_StepCtx":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            self.row.status = RestoreStepStatus.SUCCESS
            self.row.message = "OK"
        else:
            self.row.status = RestoreStepStatus.ERROR
            self.row.message = str(exc)[:2000]
        self.db.commit()
        return False


def _netapp_service_for(cluster: NetAppCluster) -> NetAppOntapService:
    if cluster.auth_method == NetAppAuthMethod.CERTIFICATE and cluster.client_cert_path and cluster.client_key_path:
        return NetAppOntapService(
            host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
            cert_path=cluster.client_cert_path, key_path=cluster.client_key_path,
        )
    return NetAppOntapService(
        host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
        username=cluster.username,
        password=decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else None,
    )


def _execute_restore(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    clone_lun_uuid: str | None = None
    netapp_service: NetAppOntapService | None = None
    svm_name: str | None = None
    igroup_name: str | None = None
    mount_point: str | None = None
    lif_address: str | None = None
    lif_port: int | None = None
    target_iqn: str | None = None

    try:
        run = db.get(RestoreRun, run_id)
        if run is None:
            return

        try:
            with _StepCtx(db, run.id, "resolve", "Ziel auflösen") as ctx:
                csv_name = _parse_csv_name(run.source_vhd_path)
                if not csv_name:
                    raise RuntimeError(f"CSV konnte nicht aus '{run.source_vhd_path}' ermittelt werden")
                csv = db.query(HyperVCsv).filter(
                    HyperVCsv.cluster_id == run.hyperv_cluster_id, HyperVCsv.name == csv_name,
                ).first()
                if csv is None or not csv.disk_serial_number:
                    raise RuntimeError(f"CSV '{csv_name}' hat keine Disk-Seriennummer (Hyper-V-Discovery prüfen)")
                # LIVE ueber die Seriennummer aufloesen, nicht ueber die bei
                # der Discovery gespeicherte HyperVCsv.netapp_lun_id -- die
                # ist eine bei jeder NetApp-Discovery neu vergebene UUID und
                # damit nach einer unabhaengigen Rediscovery veraltet (siehe
                # Chat-Verlauf, identischer Bug wurde bereits in jobs.py
                # gefunden und behoben).
                lun = db.query(NetAppLun).filter(NetAppLun.serial_number == csv.disk_serial_number).first()
                if lun is None or not lun.volume_name or not lun.svm_name:
                    raise RuntimeError(
                        f"Keine passende NetApp-LUN für CSV '{csv_name}' gefunden (Seriennummer "
                        f"{csv.disk_serial_number}) -- NetApp-Cluster erneut discovern?"
                    )
                snapshot = db.get(BackupRunSnapshot, run.source_snapshot_id) if run.source_snapshot_id else None
                if snapshot is None or not snapshot.snapshot_name:
                    raise RuntimeError("Gewählter Snapshot nicht gefunden")

                netapp_cluster = db.get(NetAppCluster, lun.cluster_id)
                if netapp_cluster is None:
                    raise RuntimeError("NetApp-Cluster der LUN nicht gefunden")
                infra_config = (
                    db.query(RestoreInfraConfig)
                    .filter(RestoreInfraConfig.netapp_cluster_id == netapp_cluster.id, RestoreInfraConfig.svm_name == lun.svm_name)
                    .first()
                )
                if infra_config is None:
                    raise RuntimeError(
                        f"Keine Restore-Infrastruktur für SVM '{lun.svm_name}' eingerichtet "
                        "(Einstellungen > Restore-Setup)."
                    )
                hv_cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                if hv_cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht gefunden")
                vm = db.query(HyperVVm).filter(
                    HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name,
                ).first()
                if vm is None or not vm.host_name:
                    raise RuntimeError(f"VM '{run.vm_name}' bzw. deren Knoten nicht gefunden")

                ctx.row.message = f"CSV {csv_name} -> Volume {lun.volume_name} @ {lun.svm_name}"

            netapp_service = _netapp_service_for(netapp_cluster)
            svm_name = lun.svm_name
            igroup_name = infra_config.igroup_name
            lif_address = infra_config.iscsi_lif_address
            lif_port = infra_config.iscsi_lif_port

            slug = _slugify(run.vm_name)
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            new_lun_name = f"restore_{slug}_{suffix}.lun"

            with _StepCtx(db, run.id, "clone-lun", "LUN aus Snapshot klonen") as ctx:
                clone = netapp_service.clone_lun_from_snapshot(
                    volume_name=lun.volume_name, svm_name=svm_name, source_lun_path=lun.name,
                    snapshot_name=snapshot.snapshot_name, new_lun_name=new_lun_name,
                )
                clone_lun_uuid = clone.uuid
                if not clone.serial_number:
                    raise RuntimeError("Geklonte LUN hat keine Seriennummer geliefert")
                ctx.row.message = f"{clone.name} (Serial {clone.serial_number})"

            with _StepCtx(db, run.id, "map-lun", "LUN der Restore-Igroup zuordnen"):
                netapp_service.create_lun_map(svm_name, clone.name, igroup_name)

            with _StepCtx(db, run.id, "iscsi-login", "iSCSI-Verbindung aufbauen") as ctx:
                target_iqn = netapp_service.get_iscsi_target_iqn(svm_name)
                exec_svc.iscsi_login(lif_address, lif_port, target_iqn)
                ctx.row.message = target_iqn

            with _StepCtx(db, run.id, "find-disk", "Disk erkennen") as ctx:
                device_path = exec_svc.find_disk_by_serial(clone.serial_number, timeout_sec=30)
                ctx.row.message = device_path

            with _StepCtx(db, run.id, "mount", "Partition einbinden") as ctx:
                partition_path = exec_svc.find_largest_partition(device_path, timeout_sec=15)
                mount_point = f"/tmp/hvnb_restore_{run.id}"
                exec_svc.mount_ntfs(partition_path, mount_point)
                ctx.row.message = partition_path

            after_csv = run.source_vhd_path.split(f"ClusterStorage\\{csv_name}\\", 1)[1]
            parts = after_csv.split("\\")
            original_filename = parts[-1]
            relative_dir = "\\".join(parts[:-1])
            stem = Path(original_filename).stem
            ext = Path(original_filename).suffix
            new_filename = f"{stem}_restore_{suffix}{ext}"

            local_dir = Path(mount_point) / relative_dir.replace("\\", "/") if relative_dir else Path(mount_point)
            local_path = local_dir / original_filename
            remote_dir = f"ClusterStorage\\{csv_name}" + (f"\\{relative_dir}" if relative_dir else "")
            restored_vhd_path = f"C:\\{remote_dir}\\{new_filename}"

            with _StepCtx(db, run.id, "copy", f"VHDX auf CSV kopieren ({new_filename})") as ctx:
                copy_result = exec_svc.copy_via_smb(
                    str(local_path), hv_cluster.management_address, hv_cluster.username,
                    decrypt_secret(hv_cluster.encrypted_password), remote_dir, new_filename,
                )
                ctx.row.message = f"{copy_result.remote_size_bytes} Bytes kopiert"
            run.restored_vhd_path = restored_vhd_path
            db.commit()

            with _StepCtx(db, run.id, "cleanup-source", "Temporäre LUN aufräumen"):
                exec_svc.unmount(mount_point)
                mount_point = None
                exec_svc.iscsi_logout(lif_address, lif_port, target_iqn)
                netapp_service.delete_lun_map(clone_lun_uuid, igroup_name, svm_name)
                netapp_service.delete_lun(clone_lun_uuid)
                clone_lun_uuid = None

            settings = get_settings()
            hv_service = HyperVService(settings, hv_cluster.management_address, use_https=hv_cluster.use_https)
            hv_password = decrypt_secret(hv_cluster.encrypted_password)

            with _StepCtx(db, run.id, "connect-node", f"Verbindung zu Knoten '{vm.host_name}'") as ctx:
                cno_session = hv_service.connect(hv_cluster.username, hv_password, read_timeout_sec=15, operation_timeout_sec=10)
                node_address = hv_service.resolve_node_address(cno_session, vm.host_name)
                node_service = HyperVService(settings, node_address, use_https=hv_cluster.use_https)
                node_session = node_service.connect(hv_cluster.username, hv_password)
                ctx.row.message = node_address

            if run.mode == RestoreMode.ADD:
                with _StepCtx(db, run.id, "attach", "VHDX als Zusatzdisk anhängen") as ctx:
                    info = node_service.attach_vhd(node_session, run.vm_name, restored_vhd_path)
                    run.attached_controller_type = str(info.get("controller_type"))
                    run.attached_controller_number = str(info.get("controller_number"))
                    run.attached_controller_location = str(info.get("controller_location"))
                    run.cleanup_needed = True
                    ctx.row.message = f"{info.get('controller_type')} {info.get('controller_number')}:{info.get('controller_location')}"
            else:
                was_running = node_service.get_vm_state(node_session, run.vm_name) == "Running"
                if was_running:
                    with _StepCtx(db, run.id, "stop-vm", "VM stoppen"):
                        result = node_service.stop_vm(node_session, run.vm_name)
                        if not result.success:
                            raise RuntimeError(result.error)
                with _StepCtx(db, run.id, "detach-old", "Alte VHDX abhängen und löschen"):
                    result = node_service.detach_vhd(node_session, run.vm_name, run.source_vhd_path)
                    if not result.success:
                        raise RuntimeError(result.error)
                    result = node_service.delete_file(node_session, run.source_vhd_path)
                    if not result.success:
                        raise RuntimeError(result.error)
                with _StepCtx(db, run.id, "attach", "Wiederhergestellte VHDX anhängen"):
                    node_service.attach_vhd(node_session, run.vm_name, restored_vhd_path)
                if was_running:
                    with _StepCtx(db, run.id, "start-vm", "VM starten"):
                        result = node_service.start_vm(node_session, run.vm_name)
                        if not result.success:
                            raise RuntimeError(result.error)

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()

        except Exception as exc:
            run.status = RestoreStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            # Best-effort Aufraeumen der temporaeren LUN, falls der Fehler
            # nach dem Klonen, aber vor dem regulaeren Cleanup-Schritt auftrat.
            if mount_point:
                exec_svc.unmount(mount_point)
            if target_iqn and lif_address and lif_port:
                exec_svc.iscsi_logout(lif_address, lif_port, target_iqn)
            if clone_lun_uuid and netapp_service and svm_name and igroup_name:
                try:
                    netapp_service.delete_lun_map(clone_lun_uuid, igroup_name, svm_name)
                except NetAppConnectionError:
                    pass
                try:
                    netapp_service.delete_lun(clone_lun_uuid)
                except NetAppConnectionError:
                    pass
    finally:
        db.close()


@router.post("/runs", response_model=RestoreRunRead, status_code=status.HTTP_202_ACCEPTED)
def trigger_restore(
    payload: TriggerRestoreRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> RestoreRun:
    snapshot = db.get(BackupRunSnapshot, payload.snapshot_id)
    if snapshot is None or not snapshot.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gewählter Snapshot ist ungültig")
    vm = db.query(HyperVVm).filter(HyperVVm.name == payload.vm_name).first()
    if vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")

    run = RestoreRun(
        hyperv_cluster_id=vm.cluster_id, vm_name=payload.vm_name, source_snapshot_id=payload.snapshot_id,
        source_vhd_path=payload.source_vhd_path, mode=payload.mode, status=RestoreStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(_execute_restore, run.id)
    return run


@router.post("/runs/{run_id}/cleanup", response_model=RestoreRunRead)
def cleanup_restore(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> RestoreRun:
    """Fuer 'add'-Restores: haengt die zusaetzlich angehaengte VHDX wieder ab
    und loescht die Datei -- der Nutzer entscheidet bewusst per Klick, wann
    das passiert (z.B. nachdem er Daten manuell aus der Zusatzdisk kopiert
    hat)."""
    run = db.get(RestoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restore-Lauf nicht gefunden")
    if not run.cleanup_needed or not run.restored_vhd_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für diesen Lauf ist kein Cleanup nötig")

    hv_cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
    if hv_cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name).first()
    if vm is None or not vm.host_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM bzw. deren Knoten nicht gefunden")

    settings = get_settings()
    hv_service = HyperVService(settings, hv_cluster.management_address, use_https=hv_cluster.use_https)
    hv_password = decrypt_secret(hv_cluster.encrypted_password)
    try:
        cno_session = hv_service.connect(hv_cluster.username, hv_password, read_timeout_sec=15, operation_timeout_sec=10)
        node_address = hv_service.resolve_node_address(cno_session, vm.host_name)
        node_service = HyperVService(settings, node_address, use_https=hv_cluster.use_https)
        node_session = node_service.connect(hv_cluster.username, hv_password)
        result = node_service.detach_vhd(node_session, run.vm_name, run.restored_vhd_path)
        if not result.success:
            raise RuntimeError(result.error)
        result = node_service.delete_file(node_session, run.restored_vhd_path)
        if not result.success:
            raise RuntimeError(result.error)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    run.cleanup_needed = False
    run.cleanup_done_at = datetime.now(timezone.utc)
    run.status = RestoreStatus.CLEANED_UP
    db.commit()
    db.refresh(run)
    return run
