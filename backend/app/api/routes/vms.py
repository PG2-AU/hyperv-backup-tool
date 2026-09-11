"""VM- und CSV-Uebersicht.

VMs und CSVs kommen aus der echten Hyper-V-Discovery (siehe
hyperv_clusters.py discover_cluster/HyperVService.run_discovery) --
persistiert in den Tabellen hyperv_vms/hyperv_vhds/hyperv_csvs, hier nur
noch zusammengefuehrt und in die bestehenden VmRead/CsvRead-Formen gebracht.
Die NetApp-LUN/-Volume-Zuordnung eines CSVs wird bereits beim Discovery-Lauf
ueber die Disk-Seriennummer aufgeloest (siehe hyperv_clusters.py); hier wird
nur noch das zugehoerige NetAppVolume fuer dessen Kapazitaet/Belegung
nachgeladen.

Resource-Group- und Policy-Zuordnung (siehe app.api.routes.resource_groups)
ist bereits real: sie wird pro VM/CSV anhand der Mitgliedschaft in
gespeicherten ResourceGroups berechnet. Eine VM gilt auch dann als
"protected", wenn sie selbst in keiner VM-Resource-Group liegt, aber auf
einem CSV liegt, das Mitglied einer CSV-Resource-Group ist (indirekter
Schutz -- die VM-Sicherung erfolgt in diesem Fall ueber das CSV-Backup).
"""

from collections import defaultdict
from datetime import datetime, timezone
from ntpath import basename as win_basename

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.hyperv_clusters import _apply_vm_discovery_refresh, _get_vm_settled
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.alert import Alert, AlertStatus, AlertType
from app.models.backup_policy import BackupPolicy, BackupScope
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.netapp_discovery import NetAppLun, NetAppVolume
from app.models.resource_group import ResourceGroup, make_member_key
from app.schemas.vm import CheckpointRead, CsvRead, NetworkAdapterRead, VhdInfo, VmRead
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/vms", tags=["vms"])


def _csv_names_for_vm(vm: VmRead) -> set[str]:
    return {win_basename(p.rstrip("\\/")) for p in vm.csv_paths}


def _member_matches(members: list[str], cluster_id: str | None, name: str) -> bool:
    """Prueft, ob ein Objekt (VM oder CSV, ueber seine cluster_id + Name)
    Mitglied einer Resource Group ist. Bevorzugt den cluster-qualifizierten
    Schluessel (siehe app.models.resource_group), faellt fuer noch nicht
    migrierte Alt-Eintraege auf den reinen Namen zurueck -- fuer die reine
    Anzeige (Badge/Filter im Inventory) ist das unkritisch, selbst wenn ein
    Alt-Eintrag inzwischen mehrdeutig waere (die tatsaechliche Backup-
    Ausfuehrung in _resolve_targets ist strikt und rät dort NICHT)."""
    if cluster_id and make_member_key(cluster_id, name) in members:
        return True
    return name in members


def _matching_policies(groups: list[ResourceGroup]) -> list[BackupPolicy]:
    by_id = {p.id: p for g in groups for p in g.policies}
    return sorted(by_id.values(), key=lambda p: p.name)


def _annotate_csv(csv: CsvRead, groups: list[ResourceGroup]) -> CsvRead:
    matching = [g for g in groups if g.scope == BackupScope.CSV and _member_matches(g.members, csv.cluster_id, csv.name)]
    group_names = sorted({g.name for g in matching})
    policies = _matching_policies(matching)
    return csv.model_copy(
        update={
            "resource_group_names": group_names,
            "policy_names": [p.name for p in policies],
            "policy_ids": [p.id for p in policies],
            "protected": bool(group_names),
        }
    )


def _annotate_vm(vm: VmRead, groups: list[ResourceGroup]) -> VmRead:
    direct = [g for g in groups if g.scope == BackupScope.VM and _member_matches(g.members, vm.cluster_id, vm.name)]

    csv_names = _csv_names_for_vm(vm)
    indirect = [
        g for g in groups
        if g.scope == BackupScope.CSV and any(_member_matches(g.members, vm.cluster_id, csv_name) for csv_name in csv_names)
    ]

    matching = direct + indirect
    group_names = sorted({g.name for g in matching})
    policies = _matching_policies(matching)
    return vm.model_copy(
        update={
            "resource_group_names": group_names,
            "policy_names": [p.name for p in policies],
            "policy_ids": [p.id for p in policies],
            "protected": bool(group_names),
        }
    )


@router.get("", response_model=list[VmRead])
def list_vms(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[VmRead]:
    groups = db.query(ResourceGroup).all()
    cluster_names = {c.id: c.name for c in db.query(HyperVCluster).all()}

    vhds_by_vm: dict[tuple[str, str | None], list[HyperVVhd]] = defaultdict(list)
    for vhd in db.query(HyperVVhd).all():
        vhds_by_vm[(vhd.cluster_id, vhd.vm_uuid)].append(vhd)

    vms: list[VmRead] = []
    for vm in db.query(HyperVVm).order_by(HyperVVm.name).all():
        vhds = vhds_by_vm.get((vm.cluster_id, vm.vm_uuid), [])
        # csv_path bleibt der CSV-ORDNERPFAD (nicht der volle VHDX-Pfad), damit
        # bestehende Basename-Logik (_csv_names_for_vm, Frontend-CSV-Gruppierung)
        # unveraendert weiterfunktioniert.
        csv_paths = sorted({f"C:\\ClusterStorage\\{v.csv_name}" for v in vhds if v.csv_name})
        vm_read = VmRead(
            id=vm.id,
            name=vm.name,
            state=vm.state or "",
            host=vm.host_name or "",
            cluster=cluster_names.get(vm.cluster_id),
            cluster_id=vm.cluster_id,
            csv_paths=csv_paths,
            vhdx_size_bytes=sum(v.size_bytes or 0 for v in vhds),
            vhdx_used_bytes=sum(v.used_bytes or 0 for v in vhds),
            vhds=[
                VhdInfo(
                    name=win_basename(v.path),
                    size_bytes=v.size_bytes or 0,
                    used_bytes=v.used_bytes,
                    csv_path=f"C:\\ClusterStorage\\{v.csv_name}" if v.csv_name else v.path,
                    full_path=v.path,
                )
                for v in vhds
            ],
            cpu_count=vm.cpu_count,
            generation=vm.generation,
            memory_startup_bytes=vm.memory_startup_bytes,
            memory_minimum_bytes=vm.memory_minimum_bytes,
            memory_maximum_bytes=vm.memory_maximum_bytes,
            dynamic_memory_enabled=vm.dynamic_memory_enabled,
            network_adapters=[NetworkAdapterRead(**n) for n in (vm.network_adapters or [])],
            pci_devices=vm.pci_devices or [],
            checkpoints=[
                CheckpointRead(name=c["name"], id=c["id"], creation_time=c["creation_time"], app_created=c["name"].startswith("hvnb_"))
                for c in (vm.checkpoints or [])
            ],
        )
        vms.append(_annotate_vm(vm_read, groups))
    return vms


@router.get("/csvs", response_model=list[CsvRead])
def list_csvs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[CsvRead]:
    groups = db.query(ResourceGroup).all()
    cluster_names = {c.id: c.name for c in db.query(HyperVCluster).all()}

    volumes_by_key: dict[tuple[str | None, str | None], NetAppVolume] = {
        (v.svm_name, v.name): v for v in db.query(NetAppVolume).all()
    }
    # Ueber serial_number matchen, NICHT ueber NetAppLun.id -- die interne
    # DB-ID wird bei JEDER NetApp-Discovery komplett neu vergeben (Loeschen +
    # Neuanlegen aller Zeilen), waehrend csv.disk_serial_number/lun.serial_number
    # dieselbe stabile Windows-Disk-/ONTAP-Seriennummer ueber Discovery-Laeufe
    # hinweg bleiben (identisches Muster wie _hyperv_referenced_keys in
    # scheduler.py). csv.netapp_lun_id (von der Hyper-V-Discovery gesetzt)
    # zeigte dadurch schon kurz nach der naechsten NetApp-Discovery ins Leere
    # -- live gefunden: lun_capacity_bytes/lun_used_bytes waren dadurch fuer
    # bereits laenger nicht neu Hyper-V-discovertes CSVs leer/falsch, obwohl
    # Storage > LUNs den korrekten Wert zeigte.
    luns_by_serial: dict[str, NetAppLun] = {lun.serial_number: lun for lun in db.query(NetAppLun).all() if lun.serial_number}

    csvs: list[CsvRead] = []
    for csv in db.query(HyperVCsv).order_by(HyperVCsv.name).all():
        volume = volumes_by_key.get((csv.netapp_svm_name, csv.netapp_volume_name)) if csv.netapp_volume_name else None
        lun = luns_by_serial.get(csv.disk_serial_number) if csv.disk_serial_number else None
        csv_read = CsvRead(
            name=csv.name,
            owner_node=csv.owner_node or "",
            state=csv.state or "",
            hyperv_cluster_name=cluster_names.get(csv.cluster_id),
            cluster_id=csv.cluster_id,
            volume_path=csv.path or "",
            capacity_bytes=csv.capacity_bytes,
            used_bytes=csv.used_bytes,
            lun_name=csv.netapp_lun_name,
            lun_capacity_bytes=lun.size_bytes if lun else None,
            lun_used_bytes=lun.used_bytes if lun else None,
            volume_name=csv.netapp_volume_name,
            volume_capacity_bytes=volume.size_bytes if volume else None,
            volume_used_bytes=volume.used_bytes if volume else None,
            svm_name=csv.netapp_svm_name,
            netapp_cluster_name=csv.netapp_cluster_name,
        )
        csvs.append(_annotate_csv(csv_read, groups))
    return csvs


@router.post("/{cluster_id}/{vm_name}/checkpoints/{checkpoint_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_vm_checkpoint(
    cluster_id: str,
    vm_name: str,
    checkpoint_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> None:
    """Loescht einen einzelnen Checkpoint einer VM -- fuer verwaiste
    Checkpoints (siehe AlertType.HYPERV_ORPHAN_CHECKPOINT in scheduler.py),
    typischerweise ein Ueberbleibsel eines abgebrochenen Backup-Laufs. Von
    Inventory > VMs UND von der Alarme-Seite aus genutzt (ein Endpunkt,
    keine doppelte Logik).

    Nutzt denselben Node-Aufloesungs-Ablauf wie die Checkpoint-Erstellung in
    _execute_job_run (jobs.py): erst CNO-Verbindung, dann live den
    aktuellen Besitzer-Knoten ermitteln (kann sich seit der letzten
    Discovery per Live-Migration/Failover geaendert haben), erst danach
    dorthin verbinden. Remove-VMSnapshot selbst ist bereits generisch ueber
    HyperVService.remove_checkpoint verfuegbar (auch fuer die normale
    Nachlauf-Bereinigung genutzt) -- hier nur neu verdrahtet."""
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
    checkpoint = next((c for c in (vm.checkpoints or []) if c["id"] == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint nicht (mehr) gefunden -- ggf. bereits geloescht")

    settings = get_settings()
    password = decrypt_secret(cluster.encrypted_password)
    refreshed_vm = None
    try:
        hv_service = HyperVService(settings, cluster.management_address, use_https=cluster.use_https)
        cno_session = hv_service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        owner_node = hv_service.get_vm_owner_node(cno_session, vm_name) or vm.host_name
        node_address = hv_service.resolve_node_address(cno_session, owner_node)
        node_service = HyperVService(settings, node_address, use_https=cluster.use_https)
        node_session = node_service.connect(cluster.username, password)
        result = node_service.remove_checkpoint(node_session, vm_name, checkpoint["name"])
        if not result.success:
            raise RuntimeError(result.error)
        # Solange ein Checkpoint besteht, zeigt Get-VM als aktive Festplatte
        # die AVHDX-Differenzdatei statt der Basis-VHDX -- Remove-VMSnapshot
        # merget sie danach zurueck, bei einer laufenden VM aber nicht immer
        # synchron (Live-Merge im Hintergrund, live beobachtet 2026-09-11:
        # bei einer ausgeschalteten VM war die VHDX direkt danach schon
        # korrekt, bei einer laufenden VM zeigte dieselbe Abfrage noch kurz
        # die AVHDX -- 4 Minuten spaeter war derselbe Merge laengst fertig).
        # _get_vm_settled() fragt deshalb mit kurzem, begrenztem Retry nach
        # (statt nur einmal), um genau dieses Fenster meist noch abzuwarten,
        # statt einen unnoetig veralteten Zwischenstand zu speichern, der
        # sonst erst mit der naechsten vollen Discovery (oder einem
        # manuellen "VM Discovery"-Klick) korrigiert wuerde. Best-effort:
        # schlaegt die Abfrage komplett fehl (der Checkpoint ist ja bereits
        # weg), bleibt der alte VHD-Stand bestehen -- kein Grund, die ganze
        # Aktion als fehlgeschlagen zu melden.
        try:
            refreshed_vm = _get_vm_settled(node_service, node_session, vm_name)
        except Exception:
            refreshed_vm = None
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Checkpoint konnte nicht geloescht werden: {exc}") from exc

    if refreshed_vm is not None:
        _apply_vm_discovery_refresh(db, cluster_id, vm, refreshed_vm)
    else:
        # Sofort aus dem discoverten Zustand entfernen, statt auf die naechste
        # Discovery zu warten -- damit verschwindet zumindest das Warn-Badge
        # in der GUI sofort, auch wenn die VHD-Ansicht (AVHDX vs. VHDX) hier
        # ausnahmsweise erst mit der naechsten Discovery nachzieht.
        vm.checkpoints = [c for c in (vm.checkpoints or []) if c["id"] != checkpoint_id]

    # Den zugehoerigen Alarm sofort mit aufloesen, statt bis zum naechsten
    # 15min-Check zu warten -- unabhaengig davon, ob die Loeschung von hier
    # (Inventory) oder von der Alarme-Seite selbst ausgeloest wurde.
    matching_alert = (
        db.query(Alert)
        .filter(Alert.alert_type == AlertType.HYPERV_ORPHAN_CHECKPOINT, Alert.checkpoint_id == checkpoint_id, Alert.status == AlertStatus.ACTIVE)
        .first()
    )
    if matching_alert is not None:
        matching_alert.status = AlertStatus.RESOLVED
        matching_alert.resolved_at = datetime.now(timezone.utc)

    db.commit()


@router.post("/{cluster_id}/{vm_name}/discover", status_code=status.HTTP_204_NO_CONTENT)
def discover_vm(
    cluster_id: str,
    vm_name: str,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> None:
    """Aktualisiert den discoverten Zustand EINER VM sofort (Get-VM inkl.
    Checkpoints/VHDs), ohne auf die naechste volle Discovery zu warten --
    Nutzer-Vorgabe 2026-09-11 als Beheben-Aktion fuer den Alarm 'AVHDX ohne
    aktiven Checkpoint' (siehe AlertType.HYPERV_VM_AVHDX_WITHOUT_CHECKPOINT
    in scheduler.py), aber generell fuer jede erkennbar veraltete VM
    nutzbar (Inventory > VMs). Nutzt denselben Node-Aufloesungs-Ablauf wie
    delete_vm_checkpoint/_execute_job_run."""
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")

    settings = get_settings()
    password = decrypt_secret(cluster.encrypted_password)
    try:
        hv_service = HyperVService(settings, cluster.management_address, use_https=cluster.use_https)
        cno_session = hv_service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        owner_node = hv_service.get_vm_owner_node(cno_session, vm_name) or vm.host_name
        node_address = hv_service.resolve_node_address(cno_session, owner_node)
        node_service = HyperVService(settings, node_address, use_https=cluster.use_https)
        node_session = node_service.connect(cluster.username, password)
        # Kurzer, begrenzter Retry statt nur einer Abfrage -- derselbe Grund
        # wie bei delete_vm_checkpoint: der AVHDX->VHDX-Merge einer
        # LAUFENDEN VM kann noch kurz nachlaufen, siehe _get_vm_settled.
        refreshed_vm = _get_vm_settled(node_service, node_session, vm_name)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"VM konnte nicht aktualisiert werden: {exc}") from exc
    if refreshed_vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM auf dem Knoten nicht (mehr) gefunden")

    # ORM-Zeile erst NACH dem WinRM-Aufruf frisch nachladen (siehe
    # _apply_vm_discovery_refresh-Docstring) -- 'vm' oben diente nur der
    # Node-Aufloesung (host_name-Fallback), nicht dem Schreiben; eine
    # zwischenzeitlich gelaufene volle Discovery hat die Zeile sonst
    # laengst geloescht+neu angelegt.
    vm_fresh = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if vm_fresh is None:
        return
    _apply_vm_discovery_refresh(db, cluster_id, vm_fresh, refreshed_vm)

    if not refreshed_vm.checkpoints:
        avhdx_alert = (
            db.query(Alert)
            .filter(
                Alert.alert_type == AlertType.HYPERV_VM_AVHDX_WITHOUT_CHECKPOINT,
                Alert.object_key == (vm_fresh.vm_uuid or ""),
                Alert.status == AlertStatus.ACTIVE,
            )
            .first()
        )
        if avhdx_alert is not None:
            avhdx_alert.status = AlertStatus.RESOLVED
            avhdx_alert.resolved_at = datetime.now(timezone.utc)

    db.commit()
