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
from app.models.backup_policy import BackupScope
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.netapp_discovery import NetAppLun, NetAppVolume
from app.models.resource_group import ResourceGroup
from app.schemas.vm import CsvRead, VhdInfo, VmRead

router = APIRouter(prefix="/api/vms", tags=["vms"])


def _csv_names_for_vm(vm: VmRead) -> set[str]:
    return {win_basename(p.rstrip("\\/")) for p in vm.csv_paths}


def _annotate_csv(csv: CsvRead, groups: list[ResourceGroup]) -> CsvRead:
    matching = [g for g in groups if g.scope == BackupScope.CSV and csv.name in g.members]
    group_names = sorted({g.name for g in matching})
    policy_names = sorted({p.name for g in matching for p in g.policies})
    return csv.model_copy(update={"resource_group_names": group_names, "policy_names": policy_names, "protected": bool(group_names)})


def _annotate_vm(vm: VmRead, groups: list[ResourceGroup]) -> VmRead:
    direct = [g for g in groups if g.scope == BackupScope.VM and vm.name in g.members]

    csv_names = _csv_names_for_vm(vm)
    indirect = [g for g in groups if g.scope == BackupScope.CSV and csv_names & set(g.members)]

    matching = direct + indirect
    group_names = sorted({g.name for g in matching})
    policy_names = sorted({p.name for g in matching for p in g.policies})
    return vm.model_copy(update={"resource_group_names": group_names, "policy_names": policy_names, "protected": bool(group_names)})


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
            csv_paths=csv_paths,
            vhdx_size_bytes=sum(v.size_bytes or 0 for v in vhds),
            vhds=[
                VhdInfo(
                    name=win_basename(v.path),
                    size_bytes=v.size_bytes or 0,
                    csv_path=f"C:\\ClusterStorage\\{v.csv_name}" if v.csv_name else v.path,
                )
                for v in vhds
            ],
        )
        vms.append(_annotate_vm(vm_read, groups))
    return vms


@router.get("/csvs", response_model=list[CsvRead])
def list_csvs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[CsvRead]:
    groups = db.query(ResourceGroup).all()

    volumes_by_key: dict[tuple[str | None, str | None], NetAppVolume] = {
        (v.svm_name, v.name): v for v in db.query(NetAppVolume).all()
    }
    luns_by_id: dict[str, NetAppLun] = {lun.id: lun for lun in db.query(NetAppLun).all()}

    csvs: list[CsvRead] = []
    for csv in db.query(HyperVCsv).order_by(HyperVCsv.name).all():
        volume = volumes_by_key.get((csv.netapp_svm_name, csv.netapp_volume_name)) if csv.netapp_volume_name else None
        lun = luns_by_id.get(csv.netapp_lun_id) if csv.netapp_lun_id else None
        csv_read = CsvRead(
            name=csv.name,
            owner_node=csv.owner_node or "",
            state=csv.state or "",
            volume_path=csv.path or "",
            capacity_bytes=csv.capacity_bytes,
            used_bytes=csv.used_bytes,
            lun_name=csv.netapp_lun_name,
            lun_capacity_bytes=lun.size_bytes if lun else None,
            lun_used_bytes=None,
            volume_name=csv.netapp_volume_name,
            volume_capacity_bytes=volume.size_bytes if volume else None,
            volume_used_bytes=volume.used_bytes if volume else None,
            svm_name=csv.netapp_svm_name,
            netapp_cluster_name=csv.netapp_cluster_name,
        )
        csvs.append(_annotate_csv(csv_read, groups))
    return csvs
