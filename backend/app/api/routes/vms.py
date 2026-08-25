"""VM- und CSV-Uebersicht.

VMs kommen inzwischen aus der echten Hyper-V-Discovery (siehe
hyperv_clusters.py discover_cluster/HyperVService.run_vm_discovery) --
persistiert in den Tabellen hyperv_vms/hyperv_vhds, hier nur noch
zusammengefuehrt und in die bestehende VmRead-Form gebracht.

TODO(iteration): CSVs sind weiterhin Demo-Daten; folgt als eigener
Discovery-Schritt (Get-ClusterSharedVolume, siehe HyperVService.list_csvs).

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
from app.models.hyperv_discovery import HyperVVhd, HyperVVm
from app.models.resource_group import ResourceGroup
from app.schemas.vm import CsvRead, VhdInfo, VmRead

router = APIRouter(prefix="/api/vms", tags=["vms"])

_DEMO_CSVS = [
    CsvRead(
        name="CSV1", owner_node="HV-NODE01", state="Online", volume_path="C:\\ClusterStorage\\CSV1",
        capacity_bytes=2_199_023_255_552, used_bytes=1_374_389_534_720,
        lun_name="lun_csv1", lun_capacity_bytes=2_199_023_255_552, lun_used_bytes=1_374_389_534_720,
        volume_name="vol_csv1", volume_capacity_bytes=2_418_925_581_107, volume_used_bytes=1_511_828_488_192,
        svm_name="svm-hyperv-prod", netapp_cluster_name="NETAPP-PROD",
    ),
    CsvRead(
        name="CSV2", owner_node="HV-NODE02", state="Online", volume_path="C:\\ClusterStorage\\CSV2",
        capacity_bytes=1_099_511_627_776, used_bytes=343_597_383_680,
        lun_name="lun_csv2", lun_capacity_bytes=1_099_511_627_776, lun_used_bytes=343_597_383_680,
        volume_name="vol_csv2", volume_capacity_bytes=1_209_462_790_554, volume_used_bytes=408_021_893_120,
        svm_name="svm-hyperv-prod", netapp_cluster_name="NETAPP-PROD",
    ),
    CsvRead(
        name="CSV3", owner_node="HV-NODE03", state="Online", volume_path="C:\\ClusterStorage\\CSV3",
        capacity_bytes=4_398_046_511_104, used_bytes=3_848_290_697_216,
        lun_name="lun_csv3", lun_capacity_bytes=4_398_046_511_104, lun_used_bytes=3_848_290_697_216,
        volume_name="vol_csv3", volume_capacity_bytes=4_837_851_162_214, volume_used_bytes=4_194_451_128_320,
        svm_name="svm-hyperv-dr", netapp_cluster_name="NETAPP-DR",
    ),
]


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
    return [_annotate_csv(csv, groups) for csv in _DEMO_CSVS]
