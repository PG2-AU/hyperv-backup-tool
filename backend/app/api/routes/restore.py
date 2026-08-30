"""VM-Restore: klont eine LUN aus einem Backup-Snapshot, meldet sich per
nativem Windows-iSCSI-Initiator auf dem Restore-Proxy-Host (Konfiguration
siehe Restore > Setup > Restore-Infrastruktur einrichten, Modell
RestoreProxyHost) an der Ziel-SVM an, kopiert die wiederhergestellte
VHDX von dort per SMB auf die Ziel-CSV, und haengt sie an die VM an (neue
Zusatzdisk, mode='add') oder ersetzt die laufende VHDX damit (mode='replace'
-- VM wird dafuer kurz gestoppt, die alte Datei wird geloescht, nicht nur
umbenannt, siehe Chat-Verlauf).

Fruehere Version fuehrte iSCSI/Mount/Kopieren per Linux-Subprocess
(iscsiadm/ntfs-3g/smbclient) direkt im Container aus -- das scheiterte an
mehreren, gegen die echte Zielumgebung verifizierten Problemen (rootless
Podman kann kein echtes CAP_SYS_ADMIN gegenueber dem init-User-Namespace
gewaehren, WSL2s Kernel-Netlink-Implementierung fuer iSCSI-Sessions
funktioniert nur im Host-Netzwerk-Namespace, kein devtmpfs fuer /dev-Knoten).
Der native Windows-iSCSI-Initiator auf einem dedizierten Windows-Host
umgeht all das strukturell.

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
from app.api.routes.hyperv_clusters import _run_discovery as _run_hyperv_discovery
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRunSnapshot, BackupRunVmConfig
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVm
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster
from app.models.netapp_discovery import NetAppLun
from app.models.restore_infra import RestoreInfraConfig
from app.models.restore_proxy_host import RestoreProxyHost
from app.models.restore_run import RestoreMode, RestoreRun, RestoreRunStep, RestoreStatus, RestoreStepStatus
from app.models.vm_recreate_run import VmRecreateRun, VmRecreateRunStep
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
    exists_in_inventory: bool = True


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


class VmBackupRunVhdRead(BaseModel):
    name: str
    size_bytes: int | None = None
    csv_name: str | None = None


class VmBackupRunNetworkAdapterRead(BaseModel):
    name: str
    switch_name: str | None = None
    vlan_id: int | None = None


class VmBackupRunRead(BaseModel):
    """Ein Backup-Lauf, der diese VM abdeckte -- fuer die 'welchen Punkt
    wiederherstellen'-Auswahl samt schreibgeschuetzter Konfigurations-
    Vorschau bei der Neuerstellung einer geloeschten VM (siehe
    VmRecreateRun). Im Unterschied zu BackupSnapshotRead (ein Snapshot = ein
    NetApp-Volume) ist das hier ein ganzer BackupRun, der bei einer mehrere
    CSVs umfassenden VM auch mehrere Snapshots umfassen kann."""

    run_id: str
    created_at: datetime
    policy_name: str
    consistency: str
    cpu_count: int | None = None
    generation: int | None = None
    memory_startup_bytes: int | None = None
    dynamic_memory_enabled: bool | None = None
    host_name: str | None = None
    network_adapters: list[VmBackupRunNetworkAdapterRead] = []
    pci_devices: list[str] = []
    vhds: list[VmBackupRunVhdRead] = []
    restore_source: str = "primary"


class VmRecreateRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class VmRecreateRunRead(BaseModel):
    id: str
    vm_name: str
    source_run_id: str
    status: str
    new_vm_uuid: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[VmRecreateRunStepRead]

    class Config:
        from_attributes = True


class RecreateVmRequest(BaseModel):
    run_id: str


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
    cluster_names = {c.id: c.name for c in db.query(HyperVCluster).all()}

    vms = db.query(HyperVVm).filter(HyperVVm.name.in_(counts.keys())).all()
    result = [
        VmWithBackupsRead(
            name=vm.name, host=vm.host_name, state=vm.state,
            cluster=cluster_names.get(vm.cluster_id), backup_count=counts.get(vm.name, 0),
            exists_in_inventory=True,
        )
        for vm in vms
    ]

    # VMs mit Backups, die aber NICHT (mehr) im aktuellen Inventory sind
    # (geloescht) -- Host/Cluster aus der zuletzt gespeicherten
    # BackupRunVmConfig ableiten, damit sie trotzdem in der Restore-Liste
    # auftauchen (siehe VmRecreateRun fuer die komplette Neuerstellung).
    missing_names = set(counts.keys()) - {vm.name for vm in vms}
    if missing_names:
        configs_by_name: dict[str, BackupRunVmConfig] = {}
        for cfg in db.query(BackupRunVmConfig).filter(BackupRunVmConfig.vm_name.in_(missing_names)).order_by(BackupRunVmConfig.created_at).all():
            configs_by_name[cfg.vm_name] = cfg  # letzte gewinnt (aufsteigend sortiert)
        for vm_name in missing_names:
            cfg = configs_by_name.get(vm_name)
            result.append(
                VmWithBackupsRead(
                    name=vm_name, host=cfg.host_name if cfg else None, state=None,
                    cluster=cluster_names.get(cfg.hyperv_cluster_id) if cfg else None,
                    backup_count=counts.get(vm_name, 0), exists_in_inventory=False,
                )
            )
    return result


@router.get("/vms/{vm_name}/backup-runs", response_model=list[VmBackupRunRead])
def list_vm_backup_runs(
    vm_name: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> list[VmBackupRunRead]:
    """Backup-Laeufe, die diese VM abdeckten -- fuer die Auswahl eines
    Wiederherstellungspunkts bei der kompletten Neuerstellung einer
    geloeschten VM. Funktioniert unabhaengig davon, ob die VM noch im
    Inventory existiert (nutzt nur BackupRunVmConfig, nicht HyperVVm).

    Analog zur Primaer-vor-Sekundaer-Regel bei Einzel-VHDX-/Datei-Restore
    (siehe list_backups_for_object in jobs.py): ein Lauf, dessen Snapshot(s)
    auf dem Primaersystem geloescht wurden, bleibt sichtbar/waehlbar,
    solange er auf einem SnapMirror-Ziel mit eingerichteter
    Restore-Infrastruktur noch vorhanden ist (restore_source='secondary').
    Ist er weder primaer noch sekundaer restorebar, wird der Lauf gar nicht
    erst angeboten -- die Neuerstellung wuerde sonst zwangslaeufig fehlschlagen."""
    infra_keys = {(c.netapp_cluster_id, c.svm_name) for c in db.query(RestoreInfraConfig).all()}

    def _restorable_destination(r: BackupRunSnapshot):
        return next(
            (d for d in r.destinations if d.present and d.destination_netapp_cluster_id and (d.destination_netapp_cluster_id, d.destination_svm_name) in infra_keys),
            None,
        )

    configs = (
        db.query(BackupRunVmConfig)
        .filter(BackupRunVmConfig.vm_name == vm_name)
        .order_by(BackupRunVmConfig.created_at.desc())
        .all()
    )

    result: list[VmBackupRunRead] = []
    for cfg in configs:
        vhd_volume_keys = {
            (v.get("netapp_cluster_id"), v.get("svm_name"), v.get("volume_name")) for v in (cfg.vhds or [])
        }
        snapshots = db.query(BackupRunSnapshot).filter(BackupRunSnapshot.run_id == cfg.run_id).all()
        relevant = [s for s in snapshots if (s.netapp_cluster_id, s.svm_name, s.volume_name) in vhd_volume_keys]
        if not relevant or not all(s.success or _restorable_destination(s) is not None for s in relevant):
            continue
        restore_source = "secondary" if any(not s.success for s in relevant) else "primary"

        result.append(
            VmBackupRunRead(
                run_id=cfg.run_id, created_at=cfg.created_at, policy_name=cfg.run.policy_name,
                consistency=cfg.run.consistency, cpu_count=cfg.cpu_count, generation=cfg.generation,
                memory_startup_bytes=cfg.memory_startup_bytes, dynamic_memory_enabled=cfg.dynamic_memory_enabled,
                host_name=cfg.host_name,
                network_adapters=[
                    VmBackupRunNetworkAdapterRead(name=n.get("name", ""), switch_name=n.get("switch_name"), vlan_id=n.get("vlan_id"))
                    for n in (cfg.network_adapters or [])
                ],
                pci_devices=cfg.pci_devices or [],
                vhds=[
                    VmBackupRunVhdRead(name=v.get("name", ""), size_bytes=v.get("size_bytes"), csv_name=v.get("csv_name"))
                    for v in (cfg.vhds or [])
                ],
                restore_source=restore_source,
            )
        )
    return result


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

    def __init__(self, db: Session, run_id: str, step_id: str, label: str, step_model: type = RestoreRunStep):
        self.db = db
        self.row = step_model(run_id=run_id, step=step_id, label=label, status=RestoreStepStatus.RUNNING)
        db.add(self.row)
        db.commit()

    def __enter__(self) -> "_StepCtx":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            self.row.status = RestoreStepStatus.SUCCESS
            # Nur auf "OK" defaulten, wenn der Aufrufer innerhalb des
            # with-Blocks keine eigene, aussagekraeftigere Nachricht gesetzt
            # hat (z.B. ctx.row.message = f"Snapshot '{name}' erstellt") --
            # frueher wurde hier bedingungslos ueberschrieben, wodurch jede
            # custom gesetzte Erfolgsmeldung verloren ging (nur bei Fehlern
            # sichtbar). Betraf bisher niemanden sichtbar, weil die
            # Restore-Wizards nur die Fehlermeldung anzeigen -- fuer das
            # neue detaillierte Backup-Job-Log (siehe logs.py) sind die
            # Erfolgsmeldungen aber der eigentliche Sinn der Anzeige.
            if not self.row.message:
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
    clone_volume_uuid: str | None = None
    used_secondary = False
    netapp_service: NetAppOntapService | None = None
    svm_name: str | None = None
    igroup_name: str | None = None
    disk_number: int | None = None
    mount_dir: str | None = None
    proxy_service: HyperVService | None = None
    proxy_session = None
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

                snapshot = db.get(BackupRunSnapshot, run.source_snapshot_id) if run.source_snapshot_id else None
                if snapshot is None or not snapshot.snapshot_name:
                    raise RuntimeError("Gewählter Snapshot nicht gefunden")

                # Bevorzugt die zum Backup-Zeitpunkt gespeicherte VHD->LUN-
                # Zuordnung nutzen (BackupRunVmConfig, siehe trigger_job_run
                # in jobs.py) statt live ueber die aktuelle Disk-Seriennummer
                # aufzuloesen -- ist die VM zwischen Backup und Restore auf
                # eine andere CSV/LUN umgezogen, wuerde die Live-Aufloesung
                # sonst faelschlich "keine passende LUN" melden, obwohl der
                # Snapshot auf dem urspruenglichen (weiterhin existierenden)
                # Volume noch vorhanden ist. Fallback auf die bisherige
                # Live-Aufloesung fuer Backups von vor diesem Feature (kein
                # BackupRunVmConfig vorhanden).
                svm_name = None
                volume_name: str | None = None
                lun_path: str | None = None
                netapp_cluster_id: str | None = None

                vm_config = (
                    db.query(BackupRunVmConfig)
                    .filter(BackupRunVmConfig.run_id == snapshot.run_id, BackupRunVmConfig.vm_name == run.vm_name)
                    .first()
                )
                if vm_config and vm_config.vhds:
                    vhd_entry = next((v for v in vm_config.vhds if v.get("path") == run.source_vhd_path), None)
                    if vhd_entry and vhd_entry.get("svm_name") and vhd_entry.get("volume_name") and vhd_entry.get("lun_name"):
                        svm_name = vhd_entry["svm_name"]
                        volume_name = vhd_entry["volume_name"]
                        lun_path = vhd_entry["lun_name"]
                        netapp_cluster_id = vhd_entry.get("netapp_cluster_id")

                # Primaer-vor-Sekundaer-Regel (Nutzer-Vorgabe): existiert der
                # Snapshot noch auf dem Primaersystem (snapshot.success wird
                # von run_snapshot_reconciliation auf False gesetzt, sobald
                # er dort extern geloescht wurde), wird IMMER von dort
                # restored. Erst wenn das nicht mehr der Fall ist, weicht der
                # Restore automatisch auf ein bekanntes, noch vorhandenes
                # SnapMirror-Ziel aus (BackupRunSnapshotDestination, siehe
                # run_snapshot_reconciliation) -- keine manuelle Auswahl noetig.
                used_secondary = False
                if svm_name and volume_name and lun_path and netapp_cluster_id and not snapshot.success:
                    usable_dest = next(
                        (d for d in snapshot.destinations if d.present and d.destination_netapp_cluster_id), None,
                    )
                    if usable_dest is None:
                        raise RuntimeError(
                            "Snapshot ist weder auf dem Primärsystem noch auf einem bekannten "
                            "SnapMirror-Ziel vorhanden"
                        )
                    # LUN-Pfad-Konvention von ONTAP: /vol/<volume>/<lun-basename> --
                    # SnapMirror spiegelt eine LUN unter demselben Basisnamen,
                    # nur der Volume-Anteil des Pfads unterscheidet sich.
                    lun_path = lun_path.replace(f"/vol/{volume_name}/", f"/vol/{usable_dest.destination_volume_name}/")
                    svm_name = usable_dest.destination_svm_name
                    volume_name = usable_dest.destination_volume_name
                    netapp_cluster_id = usable_dest.destination_netapp_cluster_id
                    used_secondary = True

                if not (svm_name and volume_name and lun_path and netapp_cluster_id):
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
                    svm_name = lun.svm_name
                    volume_name = lun.volume_name
                    lun_path = lun.name
                    netapp_cluster_id = lun.cluster_id

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

                system_label = "Sekundärsystem (SnapMirror-Ziel)" if used_secondary else "Primärsystem"
                ctx.row.message = f"CSV {csv_name} -> Volume {volume_name} @ {svm_name} ({system_label})"

            settings = get_settings()
            proxy = db.query(RestoreProxyHost).first()
            if proxy is None or not proxy.address or not proxy.username:
                raise RuntimeError(
                    "Kein Restore-Proxy-Host konfiguriert (Restore > Setup > Restore-Infrastruktur einrichten)."
                )
            hv_service = HyperVService(settings, hv_cluster.management_address, use_https=hv_cluster.use_https)
            hv_password = decrypt_secret(hv_cluster.encrypted_password)

            with _StepCtx(db, run.id, "connect-node", f"Verbindung zu Knoten '{vm.host_name}'") as ctx:
                cno_session = hv_service.connect(hv_cluster.username, hv_password, read_timeout_sec=15, operation_timeout_sec=10)
                # Die administrative C$-Freigabe fuer den SMB-Kopiervorgang
                # existiert nur auf einem echten Knoten, nicht auf dem
                # Cluster-Zugriffspunkt (hv_cluster.management_address) --
                # sonst schlaegt der Tree-Connect mit NT_STATUS_BAD_NETWORK_NAME
                # fehl (gegen echten Cluster verifiziert). Live-Migration/
                # Failover kann die VM seit der letzten Discovery auf einen
                # anderen Knoten verschoben haben, daher den aktuellen
                # Besitzer-Knoten LIVE abfragen statt der ggf. veralteten
                # HyperVVm.host_name blind zu vertrauen (verifiziert live:
                # Get-VM auf dem laut DB-Stand 'richtigen' Knoten fand die
                # VM nicht mehr).
                owner_node = hv_service.get_vm_owner_node(cno_session, run.vm_name) or vm.host_name
                node_address = hv_service.resolve_node_address(cno_session, owner_node)
                node_service = HyperVService(settings, node_address, use_https=hv_cluster.use_https)
                node_session = node_service.connect(hv_cluster.username, hv_password)
                ctx.row.message = node_address

            with _StepCtx(db, run.id, "connect-proxy", "Verbindung zum Restore-Proxy-Host") as ctx:
                proxy_service = HyperVService(settings, proxy.address, use_https=proxy.use_https)
                proxy_password = decrypt_secret(proxy.encrypted_password) if proxy.encrypted_password else ""
                proxy_session = proxy_service.connect(proxy.username, proxy_password)
                ctx.row.message = proxy.address

            netapp_service = _netapp_service_for(netapp_cluster)
            igroup_name = infra_config.igroup_name
            lif_address = infra_config.iscsi_lif_address
            lif_port = infra_config.iscsi_lif_port

            slug = _slugify(run.vm_name)
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            new_lun_name = f"restore_{slug}_{suffix}.lun"

            with _StepCtx(db, run.id, "clone-lun", "LUN aus Snapshot klonen") as ctx:
                if used_secondary:
                    # SnapMirror-Ziel ist ein DP-Volume -- LUNs lassen sich
                    # dort NICHT direkt per Snapshot klonen ('This operation
                    # is supported only on volumes of type "RW"', live
                    # verifiziert). Stattdessen wird das gesamte DP-Volume
                    # per FlexClone geklont (ergibt ein unabhaengiges
                    # RW-Volume mit der LUN bereits fertig darin), die LUN
                    # danach im Klon per Pfad gefunden statt neu geklont.
                    clone_volume_name = f"restore_{slug}_{suffix}"
                    clone_volume_uuid = netapp_service.clone_volume_from_snapshot(
                        svm_name=svm_name, source_volume_name=volume_name,
                        snapshot_name=snapshot.snapshot_name, new_volume_name=clone_volume_name,
                    )
                    cloned_lun_path = lun_path.replace(f"/vol/{volume_name}/", f"/vol/{clone_volume_name}/")
                    clone = netapp_service.find_lun_by_path(svm_name, cloned_lun_path)
                    ctx.row.message = f"Volume-Klon '{clone_volume_name}' -> {clone.name} (Serial {clone.serial_number})"
                else:
                    clone = netapp_service.clone_lun_from_snapshot(
                        volume_name=volume_name, svm_name=svm_name, source_lun_path=lun_path,
                        snapshot_name=snapshot.snapshot_name, new_lun_name=new_lun_name,
                    )
                    ctx.row.message = f"{clone.name} (Serial {clone.serial_number})"
                clone_lun_uuid = clone.uuid
                if not clone.serial_number:
                    raise RuntimeError("Geklonte LUN hat keine Seriennummer geliefert")

            with _StepCtx(db, run.id, "map-lun", "LUN der Restore-Igroup zuordnen"):
                netapp_service.create_lun_map(svm_name, clone.name, igroup_name)

            with _StepCtx(db, run.id, "iscsi-login", "iSCSI-Verbindung aufbauen") as ctx:
                target_iqn = netapp_service.get_iscsi_target_iqn(svm_name)
                proxy_service.iscsi_connect(proxy_session, lif_address, lif_port, target_iqn)
                ctx.row.message = target_iqn

            with _StepCtx(db, run.id, "find-disk", "Disk erkennen") as ctx:
                disk_number = proxy_service.find_disk_by_serial(proxy_session, clone.serial_number, timeout_sec=30)
                ctx.row.message = f"Disk {disk_number}"

            with _StepCtx(db, run.id, "mount", "Partition einbinden") as ctx:
                mount_dir = f"C:\\hvnb_restore\\{run.id}"
                mount_dir = proxy_service.prepare_data_partition_path(proxy_session, disk_number, mount_dir)
                ctx.row.message = mount_dir

            after_csv = run.source_vhd_path.split(f"ClusterStorage\\{csv_name}\\", 1)[1]
            parts = after_csv.split("\\")
            original_filename = parts[-1]
            relative_dir = "\\".join(parts[:-1])
            stem = Path(original_filename).stem
            ext = Path(original_filename).suffix
            new_filename = f"{stem}_restore_{suffix}{ext}"

            local_path = f"{mount_dir}\\{relative_dir}\\{original_filename}" if relative_dir else f"{mount_dir}\\{original_filename}"
            remote_dir = f"ClusterStorage\\{csv_name}" + (f"\\{relative_dir}" if relative_dir else "")
            restored_vhd_path = f"C:\\{remote_dir}\\{new_filename}"

            with _StepCtx(db, run.id, "copy", f"VHDX auf CSV kopieren ({new_filename})") as ctx:
                remote_size = proxy_service.copy_file_to_share(
                    proxy_session, local_path, node_address, remote_dir, new_filename,
                    hv_cluster.username, hv_password,
                )
                ctx.row.message = f"{remote_size} Bytes kopiert"
            run.restored_vhd_path = restored_vhd_path
            db.commit()

            with _StepCtx(db, run.id, "cleanup-source", "Temporäre LUN aufräumen"):
                proxy_service.release_disk(proxy_session, disk_number, mount_dir)
                disk_number = None
                mount_dir = None
                proxy_service.iscsi_disconnect(proxy_session, target_iqn)
                netapp_service.delete_lun_map(clone_lun_uuid, igroup_name, svm_name)
                if used_secondary:
                    # Beim Sekundaer-Pfad wurde kein eigenstaendiger LUN-Klon
                    # angelegt, sondern das gesamte Volume geklont -- dessen
                    # Loeschen entfernt die darin enthaltene LUN gleich mit.
                    netapp_service.delete_volume(clone_volume_uuid)
                    clone_volume_uuid = None
                else:
                    netapp_service.delete_lun(clone_lun_uuid)
                clone_lun_uuid = None

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
                with _StepCtx(db, run.id, "rename", "Wiederhergestellte VHDX umbenennen") as ctx:
                    final_path = f"C:\\{remote_dir}\\{original_filename}"
                    result = node_service.rename_file(node_session, restored_vhd_path, final_path)
                    if not result.success:
                        raise RuntimeError(result.error)
                    restored_vhd_path = final_path
                    run.restored_vhd_path = restored_vhd_path
                    db.commit()
                    ctx.row.message = restored_vhd_path
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
            if proxy_service and proxy_session and disk_number is not None and mount_dir:
                proxy_service.release_disk(proxy_session, disk_number, mount_dir)
            if proxy_service and proxy_session and target_iqn:
                proxy_service.iscsi_disconnect(proxy_session, target_iqn)
            if clone_lun_uuid and netapp_service and svm_name and igroup_name:
                try:
                    netapp_service.delete_lun_map(clone_lun_uuid, igroup_name, svm_name)
                except NetAppConnectionError:
                    pass
                if not used_secondary:
                    try:
                        netapp_service.delete_lun(clone_lun_uuid)
                    except NetAppConnectionError:
                        pass
            if used_secondary and clone_volume_uuid and netapp_service:
                try:
                    netapp_service.delete_volume(clone_volume_uuid)
                except NetAppConnectionError:
                    pass
    finally:
        db.close()


def _execute_vm_recreate(run_id: str) -> None:  # noqa: C901
    """Erstellt eine komplett geloeschte VM aus einer gespeicherten
    BackupRunVmConfig neu: pro VHD derselbe LUN-Klon/iSCSI/Kopier-Zyklus wie
    beim normalen Einzel-VHDX-Restore (_execute_restore), danach VM
    anlegen, VHDs anhaengen, Hardware/Netzwerk konfigurieren und als
    Cluster-Rolle registrieren. Anders als beim temporaeren LUN-Klon wird
    eine bereits (teilweise) angelegte VM bei einem spaeteren Fehler NICHT
    automatisch geloescht -- der Zustand bleibt fuer eine manuelle Pruefung
    sichtbar."""
    db = SessionLocal()
    try:
        run = db.get(VmRecreateRun, run_id)
        if run is None:
            return

        try:
            with _StepCtx(db, run.id, "resolve", "Backup-Konfiguration laden", step_model=VmRecreateRunStep) as ctx:
                vm_config = (
                    db.query(BackupRunVmConfig)
                    .filter(BackupRunVmConfig.run_id == run.source_run_id, BackupRunVmConfig.vm_name == run.vm_name)
                    .first()
                )
                if vm_config is None or not vm_config.vhds:
                    raise RuntimeError("Keine gespeicherte VM-Konfiguration fuer diesen Backup-Lauf gefunden")
                existing = db.query(HyperVVm).filter(
                    HyperVVm.cluster_id == run.hyperv_cluster_id, HyperVVm.name == run.vm_name,
                ).first()
                if existing is not None:
                    raise RuntimeError(f"VM '{run.vm_name}' existiert bereits -- normalen Restore statt Neuerstellung nutzen")

                snapshots = db.query(BackupRunSnapshot).filter(BackupRunSnapshot.run_id == run.source_run_id).all()
                snapshot_by_volume = {(s.netapp_cluster_id, s.svm_name, s.volume_name): s for s in snapshots}

                hv_cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                if hv_cluster is None:
                    raise RuntimeError("Hyper-V-Cluster nicht gefunden")
                proxy = db.query(RestoreProxyHost).first()
                if proxy is None or not proxy.address or not proxy.username:
                    raise RuntimeError("Kein Restore-Proxy-Host konfiguriert (Restore > Setup > Restore-Infrastruktur einrichten).")

                settings = get_settings()
                hv_service = HyperVService(settings, hv_cluster.management_address, use_https=hv_cluster.use_https)
                hv_password = decrypt_secret(hv_cluster.encrypted_password)
                ctx.row.message = f"{len(vm_config.vhds)} VHD(s), urspruenglicher Host {vm_config.host_name}"

            with _StepCtx(db, run.id, "connect-node", f"Verbindung zu Knoten '{vm_config.host_name}'", step_model=VmRecreateRunStep) as ctx:
                cno_session = hv_service.connect(hv_cluster.username, hv_password, read_timeout_sec=15, operation_timeout_sec=10)
                node_address = hv_service.resolve_node_address(cno_session, vm_config.host_name or "")
                node_service = HyperVService(settings, node_address, use_https=hv_cluster.use_https)
                node_session = node_service.connect(hv_cluster.username, hv_password)
                ctx.row.message = node_address

            with _StepCtx(db, run.id, "connect-proxy", "Verbindung zum Restore-Proxy-Host", step_model=VmRecreateRunStep) as ctx:
                proxy_service = HyperVService(settings, proxy.address, use_https=proxy.use_https)
                proxy_password = decrypt_secret(proxy.encrypted_password) if proxy.encrypted_password else ""
                proxy_session = proxy_service.connect(proxy.username, proxy_password)
                ctx.row.message = proxy.address

            restored_paths: list[str] = []
            for i, vhd in enumerate(vm_config.vhds, start=1):
                vhd_svm = vhd.get("svm_name")
                vhd_volume = vhd.get("volume_name")
                vhd_lun_path = vhd.get("lun_name")
                vhd_cluster_id = vhd.get("netapp_cluster_id")
                vhd_csv = vhd.get("csv_name")
                vhd_name = vhd.get("name") or f"disk{i}.vhdx"
                if not (vhd_svm and vhd_volume and vhd_lun_path and vhd_cluster_id and vhd_csv):
                    raise RuntimeError(f"VHD '{vhd_name}': unvollstaendige gespeicherte Zuordnung")

                snapshot = snapshot_by_volume.get((vhd_cluster_id, vhd_svm, vhd_volume))
                if snapshot is None or not snapshot.snapshot_name:
                    raise RuntimeError(f"VHD '{vhd_name}': kein passender Snapshot in diesem Backup-Lauf gefunden")

                # Primaer-vor-Sekundaer-Regel (Nutzer-Vorgabe, siehe
                # _execute_restore): existiert der Snapshot nicht mehr auf
                # dem Primaersystem (snapshot.success == False, siehe
                # run_snapshot_reconciliation), automatisch auf ein noch
                # vorhandenes SnapMirror-Ziel ausweichen -- keine manuelle
                # Auswahl noetig. Bisher fehlte dieser Fallback hier
                # komplett, eine VM-Neuerstellung aus einem nur noch
                # sekundaer vorhandenen Snapshot schlug daher fehl.
                used_secondary = False
                if not snapshot.success:
                    usable_dest = next(
                        (d for d in snapshot.destinations if d.present and d.destination_netapp_cluster_id), None,
                    )
                    if usable_dest is None:
                        raise RuntimeError(
                            f"VHD '{vhd_name}': Snapshot ist weder auf dem Primärsystem noch auf einem "
                            "bekannten SnapMirror-Ziel vorhanden"
                        )
                    vhd_lun_path = vhd_lun_path.replace(f"/vol/{vhd_volume}/", f"/vol/{usable_dest.destination_volume_name}/")
                    vhd_svm = usable_dest.destination_svm_name
                    vhd_volume = usable_dest.destination_volume_name
                    vhd_cluster_id = usable_dest.destination_netapp_cluster_id
                    used_secondary = True

                netapp_cluster = db.get(NetAppCluster, vhd_cluster_id)
                if netapp_cluster is None:
                    raise RuntimeError(f"VHD '{vhd_name}': NetApp-Cluster nicht gefunden")
                infra_config = (
                    db.query(RestoreInfraConfig)
                    .filter(RestoreInfraConfig.netapp_cluster_id == netapp_cluster.id, RestoreInfraConfig.svm_name == vhd_svm)
                    .first()
                )
                if infra_config is None:
                    raise RuntimeError(f"Keine Restore-Infrastruktur fuer SVM '{vhd_svm}' eingerichtet.")

                netapp_service = _netapp_service_for(netapp_cluster)
                igroup_name = infra_config.igroup_name
                lif_address = infra_config.iscsi_lif_address
                lif_port = infra_config.iscsi_lif_port

                slug = _slugify(run.vm_name)
                suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                new_lun_name = f"recreate_{slug}_{i}_{suffix}.lun"

                clone_lun_uuid: str | None = None
                clone_volume_uuid: str | None = None
                disk_number: int | None = None
                mount_dir: str | None = None
                target_iqn: str | None = None
                try:
                    with _StepCtx(db, run.id, f"clone-lun-{i}", f"LUN fuer {vhd_name} klonen", step_model=VmRecreateRunStep) as ctx:
                        if used_secondary:
                            # SnapMirror-Ziel ist ein DP-Volume -- LUNs lassen
                            # sich dort nicht direkt per Snapshot klonen,
                            # daher das gesamte Volume per FlexClone klonen
                            # (siehe _execute_restore fuer Details/Herkunft).
                            clone_volume_name = f"recreate_{slug}_{i}_{suffix}"
                            clone_volume_uuid = netapp_service.clone_volume_from_snapshot(
                                svm_name=vhd_svm, source_volume_name=vhd_volume,
                                snapshot_name=snapshot.snapshot_name, new_volume_name=clone_volume_name,
                            )
                            cloned_lun_path = vhd_lun_path.replace(f"/vol/{vhd_volume}/", f"/vol/{clone_volume_name}/")
                            clone = netapp_service.find_lun_by_path(vhd_svm, cloned_lun_path)
                            ctx.row.message = f"Volume-Klon '{clone_volume_name}' -> {clone.name} (Serial {clone.serial_number}, Sekundärsystem)"
                        else:
                            clone = netapp_service.clone_lun_from_snapshot(
                                volume_name=vhd_volume, svm_name=vhd_svm, source_lun_path=vhd_lun_path,
                                snapshot_name=snapshot.snapshot_name, new_lun_name=new_lun_name,
                            )
                            ctx.row.message = f"{clone.name} (Serial {clone.serial_number})"
                        clone_lun_uuid = clone.uuid
                        if not clone.serial_number:
                            raise RuntimeError("Geklonte LUN hat keine Seriennummer geliefert")

                    with _StepCtx(db, run.id, f"map-lun-{i}", f"LUN fuer {vhd_name} der Restore-Igroup zuordnen", step_model=VmRecreateRunStep):
                        netapp_service.create_lun_map(vhd_svm, clone.name, igroup_name)

                    with _StepCtx(db, run.id, f"iscsi-login-{i}", f"iSCSI-Verbindung fuer {vhd_name} aufbauen", step_model=VmRecreateRunStep) as ctx:
                        target_iqn = netapp_service.get_iscsi_target_iqn(vhd_svm)
                        proxy_service.iscsi_connect(proxy_session, lif_address, lif_port, target_iqn)
                        ctx.row.message = target_iqn

                    with _StepCtx(db, run.id, f"find-disk-{i}", f"Disk fuer {vhd_name} erkennen", step_model=VmRecreateRunStep) as ctx:
                        disk_number = proxy_service.find_disk_by_serial(proxy_session, clone.serial_number, timeout_sec=30)
                        ctx.row.message = f"Disk {disk_number}"

                    with _StepCtx(db, run.id, f"mount-{i}", f"Partition fuer {vhd_name} einbinden", step_model=VmRecreateRunStep) as ctx:
                        mount_dir = f"C:\\hvnb_restore\\{run.id}_{i}"
                        mount_dir = proxy_service.prepare_data_partition_path(proxy_session, disk_number, mount_dir)
                        ctx.row.message = mount_dir

                    original_path = vhd.get("path") or ""
                    split_marker = f"ClusterStorage\\{vhd_csv}\\"
                    after_csv = original_path.split(split_marker, 1)
                    relative_dir = ""
                    filename = vhd_name
                    if len(after_csv) == 2:
                        rel_parts = after_csv[1].split("\\")
                        filename = rel_parts[-1]
                        relative_dir = "\\".join(rel_parts[:-1])
                    local_path = f"{mount_dir}\\{relative_dir}\\{filename}" if relative_dir else f"{mount_dir}\\{filename}"
                    remote_dir = f"ClusterStorage\\{vhd_csv}" + (f"\\{relative_dir}" if relative_dir else "")

                    with _StepCtx(db, run.id, f"copy-{i}", f"{vhd_name} auf CSV kopieren", step_model=VmRecreateRunStep) as ctx:
                        remote_size = proxy_service.copy_file_to_share(
                            proxy_session, local_path, node_address, remote_dir, filename,
                            hv_cluster.username, hv_password,
                        )
                        ctx.row.message = f"{remote_size} Bytes kopiert"
                    restored_paths.append(f"C:\\{remote_dir}\\{filename}")

                    with _StepCtx(db, run.id, f"cleanup-source-{i}", f"Temporaere LUN fuer {vhd_name} aufraeumen", step_model=VmRecreateRunStep):
                        proxy_service.release_disk(proxy_session, disk_number, mount_dir)
                        disk_number = None
                        mount_dir = None
                        proxy_service.iscsi_disconnect(proxy_session, target_iqn)
                        netapp_service.delete_lun_map(clone_lun_uuid, igroup_name, vhd_svm)
                        if used_secondary:
                            # Beim Sekundaer-Pfad wurde kein eigenstaendiger
                            # LUN-Klon angelegt, sondern das gesamte Volume
                            # geklont -- dessen Loeschen entfernt die darin
                            # enthaltene LUN gleich mit.
                            netapp_service.delete_volume(clone_volume_uuid)
                            clone_volume_uuid = None
                        else:
                            netapp_service.delete_lun(clone_lun_uuid)
                        clone_lun_uuid = None
                except Exception:
                    if disk_number is not None and mount_dir:
                        proxy_service.release_disk(proxy_session, disk_number, mount_dir)
                    if target_iqn:
                        proxy_service.iscsi_disconnect(proxy_session, target_iqn)
                    if clone_lun_uuid:
                        try:
                            netapp_service.delete_lun_map(clone_lun_uuid, igroup_name, vhd_svm)
                        except NetAppConnectionError:
                            pass
                        if not used_secondary:
                            try:
                                netapp_service.delete_lun(clone_lun_uuid)
                            except NetAppConnectionError:
                                pass
                    if used_secondary and clone_volume_uuid:
                        try:
                            netapp_service.delete_volume(clone_volume_uuid)
                        except NetAppConnectionError:
                            pass
                    raise

            first_csv = vm_config.vhds[0].get("csv_name")
            storage_path = f"C:\\ClusterStorage\\{first_csv}\\{run.vm_name}"
            with _StepCtx(db, run.id, "create-vm", "VM anlegen", step_model=VmRecreateRunStep) as ctx:
                new_vm_uuid = node_service.create_vm(node_session, run.vm_name, vm_config.generation or 2, storage_path)
                run.new_vm_uuid = new_vm_uuid
                db.commit()
                ctx.row.message = new_vm_uuid

            with _StepCtx(db, run.id, "attach-disks", "Wiederhergestellte VHDs anhaengen", step_model=VmRecreateRunStep):
                for path in restored_paths:
                    node_service.attach_vhd(node_session, run.vm_name, path)

            with _StepCtx(db, run.id, "configure-hardware", "CPU/RAM konfigurieren", step_model=VmRecreateRunStep):
                node_service.configure_vm_hardware(
                    node_session, run.vm_name, vm_config.cpu_count,
                    vm_config.memory_startup_bytes, vm_config.memory_minimum_bytes, vm_config.memory_maximum_bytes,
                    vm_config.dynamic_memory_enabled,
                )

            if vm_config.network_adapters:
                with _StepCtx(db, run.id, "configure-network", "Netzwerkadapter anlegen", step_model=VmRecreateRunStep):
                    for nic in vm_config.network_adapters:
                        if nic.get("switch_name"):
                            node_service.add_network_adapter(node_session, run.vm_name, nic["switch_name"], nic.get("vlan_id"))

            with _StepCtx(db, run.id, "register-cluster-role", "Als Cluster-Rolle registrieren", step_model=VmRecreateRunStep):
                hv_service.register_cluster_role(cno_session, run.vm_name)

            with _StepCtx(db, run.id, "post-discovery", "Inventory aktualisieren", step_model=VmRecreateRunStep) as ctx:
                # Best-effort: die VM ist zu diesem Zeitpunkt bereits
                # erfolgreich neu erstellt, ein Fehler hier soll den Lauf
                # nicht nachtraeglich als failed markieren -- sonst muesste
                # man sonst bis zum naechsten periodischen Discovery-Lauf
                # (Standard alle 4h, siehe app.core.scheduler) oder einem
                # manuellen Discover warten, bis die VM im Inventory
                # auftaucht.
                try:
                    _run_hyperv_discovery(db, hv_cluster)
                    ctx.row.message = "Discovery abgeschlossen"
                except Exception as exc:
                    ctx.row.message = f"Discovery fehlgeschlagen (VM wurde trotzdem erfolgreich erstellt): {exc}"

            run.status = RestoreStatus.SUCCEEDED
            run.finished_at = datetime.now(timezone.utc)
            db.commit()

        except Exception as exc:
            run.status = RestoreStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/vms/{vm_name}/recreate", response_model=VmRecreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def recreate_vm(
    vm_name: str, payload: RecreateVmRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> VmRecreateRun:
    vm_config = (
        db.query(BackupRunVmConfig)
        .filter(BackupRunVmConfig.run_id == payload.run_id, BackupRunVmConfig.vm_name == vm_name)
        .first()
    )
    if vm_config is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine gespeicherte VM-Konfiguration fuer diesen Backup-Lauf gefunden")
    if not vm_config.hyperv_cluster_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kein Hyper-V-Cluster fuer diese VM-Konfiguration bekannt")

    run = VmRecreateRun(
        hyperv_cluster_id=vm_config.hyperv_cluster_id, vm_name=vm_name, source_run_id=payload.run_id,
        status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(_execute_vm_recreate, run.id)
    return run


@router.get("/vm-recreate-runs/{run_id}", response_model=VmRecreateRunRead)
def get_vm_recreate_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> VmRecreateRun:
    run = db.get(VmRecreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.post("/runs", response_model=RestoreRunRead, status_code=status.HTTP_202_ACCEPTED)
def trigger_restore(
    payload: TriggerRestoreRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.RESTORE_RUN)),
) -> RestoreRun:
    snapshot = db.get(BackupRunSnapshot, payload.snapshot_id)
    # Nicht mehr nur success=True akzeptieren: ein auf dem Primaersystem
    # geloeschter Snapshot (success=False) ist weiterhin restorebar, wenn
    # er auf einem bekannten SnapMirror-Ziel noch vorhanden ist -- der
    # eigentliche Primaer-vor-Sekundaer-Entscheid faellt dann live im
    # resolve-Schritt von _execute_restore (siehe dort).
    if snapshot is None or not (snapshot.success or any(d.present and d.destination_netapp_cluster_id for d in snapshot.destinations)):
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
        owner_node = hv_service.get_vm_owner_node(cno_session, run.vm_name) or vm.host_name
        node_address = hv_service.resolve_node_address(cno_session, owner_node)
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
