"""VM- und CSV-Uebersicht.

TODO(iteration): Aktuell werden Demo-Daten zurueckgegeben, damit die GUI
gegen eine stabile Schnittstelle entwickelt werden kann. Anbindung an
HyperVService.list_vms/list_csvs folgt, sobald die Host-Verwaltung
(gespeicherte Hyper-V-Hosts/Cluster + Credentials) implementiert ist.
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.schemas.vm import CsvRead, VmRead

router = APIRouter(prefix="/api/vms", tags=["vms"])

_DEMO_VMS = [
    VmRead(
        id="vm-001", name="APP-SQL01", state="Running", host="HV-NODE01", cluster="HV-CLUSTER01",
        csv_paths=["C:\\ClusterStorage\\CSV1"], vhdx_size_bytes=536_870_912_000,
        backup_policy_id="job-001", backup_policy_name="SQL-Cluster taeglich 02:00",
    ),
    VmRead(
        id="vm-002", name="APP-WEB01", state="Running", host="HV-NODE02", cluster="HV-CLUSTER01",
        csv_paths=["C:\\ClusterStorage\\CSV2"], vhdx_size_bytes=107_374_182_400,
    ),
    VmRead(
        id="vm-003", name="DC-01", state="Running", host="HV-NODE01", cluster="HV-CLUSTER01",
        csv_paths=["C:\\ClusterStorage\\CSV1"], vhdx_size_bytes=64_424_509_440,
    ),
    VmRead(
        id="vm-004", name="FILESRV-01", state="Off", host="HV-NODE03", cluster="HV-CLUSTER02",
        csv_paths=["C:\\ClusterStorage\\CSV3"], vhdx_size_bytes=2_199_023_255_552,
    ),
]

_DEMO_CSVS = [
    CsvRead(
        name="CSV1", owner_node="HV-NODE01", state="Online", volume_path="C:\\ClusterStorage\\CSV1",
        capacity_bytes=2_199_023_255_552, used_bytes=1_374_389_534_720,
        backup_policy_id="job-002", backup_policy_name="CSV1 stuendlich",
    ),
    CsvRead(
        name="CSV2", owner_node="HV-NODE02", state="Online", volume_path="C:\\ClusterStorage\\CSV2",
        capacity_bytes=1_099_511_627_776, used_bytes=343_597_383_680,
    ),
    CsvRead(
        name="CSV3", owner_node="HV-NODE03", state="Online", volume_path="C:\\ClusterStorage\\CSV3",
        capacity_bytes=4_398_046_511_104, used_bytes=3_848_290_697_216,
    ),
]


@router.get("", response_model=list[VmRead])
def list_vms(user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[VmRead]:
    return _DEMO_VMS


@router.get("/csvs", response_model=list[CsvRead])
def list_csvs(user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[CsvRead]:
    return _DEMO_CSVS
