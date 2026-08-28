"""Datei-basierter Restore: mountet eine VHDX aus einem Backup-Snapshot
direkt auf dem Restore-Proxy-Host (LUN-Klon -> iSCSI -> Partition
einbinden -> Mount-VHD -> Partition der VHDX einbinden) und macht ihr
Dateisystem darüber per GUI durchsuchbar -- ohne die ganze Platte auf eine
CSV zu kopieren oder an eine VM anzuhängen (siehe app.api.routes.restore
für diesen bisherigen Weg).

Nutzt bewusst ausschliesslich die zum Backup-Zeitpunkt gespeicherte
VHD->LUN-Zuordnung (BackupRunVmConfig, siehe trigger_job_run in jobs.py),
keinen Live-Fallback über die aktuelle Disk-Seriennummer wie
_execute_restore ihn hat -- Datei-Restore braucht dadurch keinen Bezug zu
HyperVCluster/HyperVVm und funktioniert damit auch für bereits gelöschte
VMs, genau wie VmRecreateRun. Fehlt die gespeicherte Zuordnung (Backups von
vor diesem Feature), wird ein klarer Fehler geworfen -- für solche Backups
bleibt der normale VHDX-Restore (anhängen/ersetzen) der Weg.

Der offene Mount bleibt bewusst bestehen (cleanup_needed=True), bis der
Nutzer ihn manuell aufräumt oder das Zeitlimit greift (siehe
app.core.scheduler.run_file_restore_expiry) -- dazwischen kann beliebig oft
durchsucht und kopiert werden, ohne neu zu mounten."""

from __future__ import annotations

import ntpath
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.restore import _StepCtx, _netapp_service_for, _parse_csv_name, _slugify
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRunSnapshot, BackupRunVmConfig
from app.models.file_restore_run import FileRestoreRun, FileRestoreRunStep
from app.models.netapp_cluster import NetAppCluster
from app.models.restore_infra import RestoreInfraConfig
from app.models.restore_proxy_host import RestoreProxyHost
from app.models.restore_run import RestoreStatus, RestoreStepStatus
from app.services.hyperv_service import HyperVService
from app.services.netapp_service import NetAppConnectionError

router = APIRouter(prefix="/api/file-restore", tags=["file-restore"])


class FileRestoreRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class FileRestoreRunRead(BaseModel):
    id: str
    vm_name: str
    source_vhd_path: str
    status: str
    browse_root_path: str | None = None
    default_destination_path: str | None = None
    cleanup_needed: bool
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[FileRestoreRunStepRead]

    class Config:
        from_attributes = True


class TriggerFileRestoreRequest(BaseModel):
    vm_name: str
    snapshot_id: str
    source_vhd_path: str


class FileEntryRead(BaseModel):
    name: str
    is_directory: bool
    size_bytes: int | None = None
    modified_at: str | None = None


class CopyFilesRequest(BaseModel):
    selected_paths: list[str]
    destination_path: str


def _validate_within_root(path: str, root: str) -> str:
    """Normalisiert einen Windows-Pfad (ntpath, unabhaengig vom Host-OS
    dieses Containers) und stellt sicher, dass er innerhalb von root liegt
    -- Pflichtpruefung vor jedem Browse/Copy, sonst waeren beliebige Pfade
    auf dem Restore-Proxy-Host lesbar/kopierbar."""
    norm_path = ntpath.normpath(path)
    norm_root = ntpath.normpath(root)
    if ntpath.normcase(norm_path) != ntpath.normcase(norm_root) and not ntpath.normcase(norm_path).startswith(
        ntpath.normcase(norm_root) + "\\"
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pfad liegt ausserhalb der gemounteten VHDX")
    return norm_path


def _connect_proxy(db: Session) -> tuple[HyperVService, object, RestoreProxyHost]:
    proxy = db.query(RestoreProxyHost).first()
    if proxy is None or not proxy.address or not proxy.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kein Restore-Proxy-Host konfiguriert (Restore > Setup > Restore-Infrastruktur einrichten).",
        )
    settings = get_settings()
    proxy_service = HyperVService(settings, proxy.address, use_https=proxy.use_https)
    proxy_password = decrypt_secret(proxy.encrypted_password) if proxy.encrypted_password else ""
    proxy_session = proxy_service.connect(proxy.username, proxy_password)
    return proxy_service, proxy_session, proxy


@router.get("/runs", response_model=list[FileRestoreRunRead])
def list_file_restore_runs(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> list[FileRestoreRun]:
    return db.query(FileRestoreRun).order_by(FileRestoreRun.started_at.desc()).all()


@router.get("/runs/{run_id}", response_model=FileRestoreRunRead)
def get_file_restore_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> FileRestoreRun:
    run = db.get(FileRestoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datei-Restore-Session nicht gefunden")
    return run


@router.post("/runs", response_model=FileRestoreRunRead, status_code=status.HTTP_202_ACCEPTED)
def trigger_file_restore(
    payload: TriggerFileRestoreRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> FileRestoreRun:
    snapshot = db.get(BackupRunSnapshot, payload.snapshot_id)
    if snapshot is None or not snapshot.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gewählter Snapshot ist ungültig")

    run = FileRestoreRun(
        vm_name=payload.vm_name, source_snapshot_id=payload.snapshot_id,
        source_vhd_path=payload.source_vhd_path, status=RestoreStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(_execute_file_restore_open, run.id)
    return run


@router.get("/runs/{run_id}/browse", response_model=list[FileEntryRead])
def browse_file_restore(
    run_id: str, path: str | None = None,
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> list[FileEntryRead]:
    run = db.get(FileRestoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datei-Restore-Session nicht gefunden")
    if not run.browse_root_path or run.status != RestoreStatus.SUCCEEDED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session ist nicht geöffnet/durchsuchbar")

    target = _validate_within_root(path or run.browse_root_path, run.browse_root_path)
    proxy_service, proxy_session, _ = _connect_proxy(db)
    try:
        entries = proxy_service.list_directory(proxy_session, target)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [FileEntryRead(**e) for e in entries]


@router.post("/runs/{run_id}/copy", status_code=status.HTTP_204_NO_CONTENT)
def copy_file_restore_selection(
    run_id: str, payload: CopyFilesRequest,
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> None:
    run = db.get(FileRestoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datei-Restore-Session nicht gefunden")
    if not run.browse_root_path or run.status != RestoreStatus.SUCCEEDED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session ist nicht geöffnet/durchsuchbar")
    if not payload.selected_paths:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine Elemente ausgewählt")

    validated = [_validate_within_root(p, run.browse_root_path) for p in payload.selected_paths]
    proxy_service, proxy_session, _ = _connect_proxy(db)
    try:
        proxy_service.copy_paths(proxy_session, validated, payload.destination_path)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _cleanup_file_restore_run(db: Session, run: FileRestoreRun) -> None:
    """Baut den temporaeren Mount/LUN-Klon wieder ab -- gemeinsam genutzt
    vom manuellen Cleanup-Endpunkt und vom automatischen Zeitlimit-Job
    (siehe app.core.scheduler.run_file_restore_expiry). Best-effort wie das
    Aufraeumen ueberall sonst in dieser App: jeder Schritt versucht sein
    Bestes, ein einzelner Fehlschlag verhindert nicht die folgenden."""
    try:
        proxy_service, proxy_session, _ = _connect_proxy(db)
    except HTTPException:
        return
    if run.vhd_file_path:
        proxy_service.dismount_vhd(proxy_session, run.vhd_file_path)
    if run.vhd_disk_number is not None and run.proxy_vhd_mount_dir:
        proxy_service.release_disk(proxy_session, run.vhd_disk_number, run.proxy_vhd_mount_dir)
    if run.disk_number is not None and run.proxy_lun_mount_dir:
        proxy_service.release_disk(proxy_session, run.disk_number, run.proxy_lun_mount_dir)
    if run.target_iqn:
        proxy_service.iscsi_disconnect(proxy_session, run.target_iqn)
    if run.clone_lun_uuid and run.svm_name and run.igroup_name and run.netapp_cluster_id:
        cluster = db.get(NetAppCluster, run.netapp_cluster_id)
        if cluster is not None:
            netapp_service = _netapp_service_for(cluster)
            try:
                netapp_service.delete_lun_map(run.clone_lun_uuid, run.igroup_name, run.svm_name)
            except NetAppConnectionError:
                pass
            try:
                netapp_service.delete_lun(run.clone_lun_uuid)
            except NetAppConnectionError:
                pass

    run.cleanup_needed = False
    run.cleanup_done_at = datetime.now(timezone.utc)
    run.status = RestoreStatus.CLEANED_UP
    db.commit()


@router.post("/runs/{run_id}/cleanup", response_model=FileRestoreRunRead)
def cleanup_file_restore(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> FileRestoreRun:
    run = db.get(FileRestoreRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datei-Restore-Session nicht gefunden")
    if not run.cleanup_needed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für diese Session ist kein Cleanup nötig")
    _cleanup_file_restore_run(db, run)
    db.refresh(run)
    return run


def _execute_file_restore_open(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    clone_lun_uuid: str | None = None
    netapp_service = None
    svm_name: str | None = None
    igroup_name: str | None = None
    disk_number: int | None = None
    vhd_disk_number: int | None = None
    lun_mount_dir: str | None = None
    vhd_mount_dir: str | None = None
    proxy_service: HyperVService | None = None
    proxy_session = None
    target_iqn: str | None = None
    netapp_cluster_id: str | None = None

    try:
        run = db.get(FileRestoreRun, run_id)
        if run is None:
            return

        try:
            with _StepCtx(db, run.id, "resolve", "Ziel auflösen", step_model=FileRestoreRunStep) as ctx:
                csv_name = _parse_csv_name(run.source_vhd_path)
                if not csv_name:
                    raise RuntimeError(f"CSV konnte nicht aus '{run.source_vhd_path}' ermittelt werden")

                snapshot = db.get(BackupRunSnapshot, run.source_snapshot_id)
                if snapshot is None or not snapshot.snapshot_name:
                    raise RuntimeError("Gewählter Snapshot nicht gefunden")

                vm_config = (
                    db.query(BackupRunVmConfig)
                    .filter(BackupRunVmConfig.run_id == snapshot.run_id, BackupRunVmConfig.vm_name == run.vm_name)
                    .first()
                )
                vhd_entry = next((v for v in (vm_config.vhds or []) if v.get("path") == run.source_vhd_path), None) if vm_config else None
                if not vhd_entry or not (vhd_entry.get("svm_name") and vhd_entry.get("volume_name") and vhd_entry.get("lun_name")):
                    raise RuntimeError(
                        "Für diesen Backup-Lauf liegt keine gespeicherte VHD-Zuordnung vor "
                        "(Backups von vor der VM-Konfigurationserfassung) -- bitte stattdessen "
                        "den normalen VHDX-Restore (Anhängen/Ersetzen) verwenden."
                    )
                svm_name = vhd_entry["svm_name"]
                volume_name = vhd_entry["volume_name"]
                lun_path = vhd_entry["lun_name"]
                netapp_cluster_id = vhd_entry.get("netapp_cluster_id")

                netapp_cluster = db.get(NetAppCluster, netapp_cluster_id)
                if netapp_cluster is None:
                    raise RuntimeError("NetApp-Cluster der LUN nicht gefunden")
                infra_config = (
                    db.query(RestoreInfraConfig)
                    .filter(RestoreInfraConfig.netapp_cluster_id == netapp_cluster.id, RestoreInfraConfig.svm_name == svm_name)
                    .first()
                )
                if infra_config is None:
                    raise RuntimeError(
                        f"Keine Restore-Infrastruktur für SVM '{svm_name}' eingerichtet "
                        "(Restore > Setup > Restore-Infrastruktur einrichten)."
                    )
                ctx.row.message = f"CSV {csv_name} -> Volume {volume_name} @ {svm_name}"

            with _StepCtx(db, run.id, "connect-proxy", "Verbindung zum Restore-Proxy-Host", step_model=FileRestoreRunStep) as ctx:
                proxy_service, proxy_session, proxy = _connect_proxy(db)
                ctx.row.message = proxy.address

            netapp_service = _netapp_service_for(netapp_cluster)
            igroup_name = infra_config.igroup_name
            lif_address = infra_config.iscsi_lif_address
            lif_port = infra_config.iscsi_lif_port

            slug = _slugify(run.vm_name)
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            new_lun_name = f"filerestore_{slug}_{suffix}.lun"

            with _StepCtx(db, run.id, "clone-lun", "LUN aus Snapshot klonen", step_model=FileRestoreRunStep) as ctx:
                clone = netapp_service.clone_lun_from_snapshot(
                    volume_name=volume_name, svm_name=svm_name, source_lun_path=lun_path,
                    snapshot_name=snapshot.snapshot_name, new_lun_name=new_lun_name,
                )
                clone_lun_uuid = clone.uuid
                if not clone.serial_number:
                    raise RuntimeError("Geklonte LUN hat keine Seriennummer geliefert")
                ctx.row.message = f"{clone.name} (Serial {clone.serial_number})"
                run.clone_lun_uuid = clone_lun_uuid
                run.clone_lun_name = clone.name
                run.netapp_cluster_id = netapp_cluster_id
                run.svm_name = svm_name
                run.igroup_name = igroup_name
                db.commit()

            with _StepCtx(db, run.id, "map-lun", "LUN der Restore-Igroup zuordnen", step_model=FileRestoreRunStep):
                netapp_service.create_lun_map(svm_name, clone.name, igroup_name)

            with _StepCtx(db, run.id, "iscsi-login", "iSCSI-Verbindung aufbauen", step_model=FileRestoreRunStep) as ctx:
                target_iqn = netapp_service.get_iscsi_target_iqn(svm_name)
                proxy_service.iscsi_connect(proxy_session, lif_address, lif_port, target_iqn)
                run.target_iqn = target_iqn
                db.commit()
                ctx.row.message = target_iqn

            with _StepCtx(db, run.id, "find-disk", "Disk erkennen", step_model=FileRestoreRunStep) as ctx:
                disk_number = proxy_service.find_disk_by_serial(proxy_session, clone.serial_number, timeout_sec=30)
                run.disk_number = disk_number
                db.commit()
                ctx.row.message = f"Disk {disk_number}"

            with _StepCtx(db, run.id, "mount-lun-partition", "LUN-Partition einbinden", step_model=FileRestoreRunStep) as ctx:
                lun_mount_dir = f"C:\\hvnb_filerestore\\{run.id}\\lun"
                lun_mount_dir = proxy_service.prepare_data_partition_path(proxy_session, disk_number, lun_mount_dir)
                run.proxy_lun_mount_dir = lun_mount_dir
                db.commit()
                ctx.row.message = lun_mount_dir

            with _StepCtx(db, run.id, "locate-vhdx", "VHDX-Datei finden", step_model=FileRestoreRunStep) as ctx:
                after_csv = run.source_vhd_path.split(f"ClusterStorage\\{csv_name}\\", 1)[1]
                parts = after_csv.split("\\")
                original_filename = parts[-1]
                relative_dir = "\\".join(parts[:-1])
                vhd_file_path = (
                    f"{lun_mount_dir}\\{relative_dir}\\{original_filename}" if relative_dir else f"{lun_mount_dir}\\{original_filename}"
                )
                run.vhd_file_path = vhd_file_path
                db.commit()
                ctx.row.message = vhd_file_path

            with _StepCtx(db, run.id, "mount-vhd", "VHDX mounten", step_model=FileRestoreRunStep) as ctx:
                vhd_disk_number = proxy_service.mount_vhd(proxy_session, vhd_file_path, read_only=True)
                run.vhd_disk_number = vhd_disk_number
                db.commit()
                ctx.row.message = f"Disk {vhd_disk_number}"

            with _StepCtx(db, run.id, "mount-vhd-partition", "VHDX-Partition einbinden", step_model=FileRestoreRunStep) as ctx:
                vhd_mount_dir = f"C:\\hvnb_filerestore\\{run.id}\\vhd"
                vhd_mount_dir = proxy_service.prepare_vhd_partition_path(proxy_session, vhd_disk_number, vhd_mount_dir)
                run.proxy_vhd_mount_dir = vhd_mount_dir
                run.browse_root_path = vhd_mount_dir
                run.default_destination_path = f"C:\\FileRestore\\{slug}\\{suffix}"
                db.commit()
                ctx.row.message = vhd_mount_dir

            run.status = RestoreStatus.SUCCEEDED
            run.cleanup_needed = True
            run.finished_at = datetime.now(timezone.utc)
            db.commit()

        except Exception as exc:
            run.status = RestoreStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            # Best-effort Aufraeumen dessen, was bereits aufgebaut wurde --
            # gleiches Muster wie im Fehlerfall von _execute_restore.
            if proxy_service and proxy_session:
                if vhd_disk_number is not None and vhd_mount_dir:
                    proxy_service.release_disk(proxy_session, vhd_disk_number, vhd_mount_dir)
                if disk_number is not None and lun_mount_dir:
                    proxy_service.release_disk(proxy_session, disk_number, lun_mount_dir)
                if target_iqn:
                    proxy_service.iscsi_disconnect(proxy_session, target_iqn)
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
