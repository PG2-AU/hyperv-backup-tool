"""NetApp-Storage-Uebersicht (SVMs, SnapMirror-Beziehungen, MetroCluster-Status).

TODO(iteration): Demo-Daten; Anbindung an NetAppOntapService folgt mit der
Cluster-Verwaltung (gespeicherte ONTAP-Verbindungen).
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.schemas.storage import MetroClusterStatus, SnapMirrorRelationshipRead, SvmInfo

router = APIRouter(prefix="/api/storage", tags=["storage"])

_DEMO_SVMS = [
    SvmInfo(name="svm-hyperv-prod", state="running", is_metrocluster=True),
    SvmInfo(name="svm-hyperv-dr", state="running", is_metrocluster=True),
]

_DEMO_RELATIONSHIPS = [
    SnapMirrorRelationshipRead(
        uuid="a1b2c3d4-0001",
        source_path="svm-hyperv-prod:vol_csv1",
        destination_path="svm-hyperv-dr:vol_csv1_dst",
        state="snapmirrored",
        healthy=True,
    ),
    SnapMirrorRelationshipRead(
        uuid="a1b2c3d4-0002",
        source_path="svm-hyperv-prod:vol_csv2",
        destination_path="svm-hyperv-dr:vol_csv2_dst",
        state="snapmirrored",
        healthy=True,
    ),
]


@router.get("/svms", response_model=list[SvmInfo])
def list_svms(user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[SvmInfo]:
    return _DEMO_SVMS


@router.get("/snapmirror-relationships", response_model=list[SnapMirrorRelationshipRead])
def list_snapmirror_relationships(user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[SnapMirrorRelationshipRead]:
    return _DEMO_RELATIONSHIPS


@router.get("/metrocluster-status", response_model=MetroClusterStatus)
def metrocluster_status(user=Depends(require_permission(Permission.STORAGE_VIEW))) -> MetroClusterStatus:
    return MetroClusterStatus(configured=True, mode="normal", switchover_in_progress=False)
