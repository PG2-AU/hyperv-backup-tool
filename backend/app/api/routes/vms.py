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
from ntpath import basename as win_basename

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy, BackupScope
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.netapp_discovery import NetAppLun, NetAppVolume
from app.models.resource_group import ResourceGroup, make_member_key
from app.schemas.vm import CsvRead, NetworkAdapterRead, VhdInfo, VmRead

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
