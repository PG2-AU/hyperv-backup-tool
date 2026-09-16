"""Kapazitaetsverlauf (Liniendiagramm) fuer VHDs, CSVs, LUNs, Volumes,
Aggregate -- ein einziger Endpunkt fuer alle fuenf Objekttypen, siehe
app.core.capacity_history fuer die Schluesselableitung und
app.core.scheduler.run_capacity_history_sampling fuer den taeglichen
Sammel-Job, der die hier abgefragten CapacitySample-Zeilen schreibt.

Leserechte richten sich nach dem jeweiligen Objekttyp: vhd/csv gehoeren
zu Hyper-V (HYPERV_VIEW), lun/volume/aggregate zu NetApp (STORAGE_VIEW)
-- dieselbe Aufteilung wie bei den jeweiligen Listen-Endpunkten."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_user_permissions
from app.core.capacity_history import CapacityObjectType, capacity_key
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.capacity_history import CapacitySample
from app.models.hyperv_discovery import HyperVVhd
from app.models.netapp_discovery import NetAppAggregate, NetAppLun, NetAppVolume
from app.models.user import User
from app.schemas.capacity_history import CapacitySamplePoint, CapacitySeries

router = APIRouter(prefix="/api/capacity-history", tags=["capacity-history"])


def _require_view(object_type: CapacityObjectType, user: User, db: Session) -> None:
    permissions = get_user_permissions(user, db)
    required = Permission.HYPERV_VIEW if object_type in ("vhd", "csv") else Permission.STORAGE_VIEW
    if required not in permissions:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung: {required.value}")


def _series_for_key(db: Session, object_type: str, key: str, name: str, since: datetime) -> CapacitySeries:
    rows = (
        db.query(CapacitySample)
        .filter(CapacitySample.object_type == object_type, CapacitySample.object_key == key, CapacitySample.sampled_at >= since)
        .order_by(CapacitySample.sampled_at)
        .all()
    )
    return CapacitySeries(
        object_key=key,
        object_name=name,
        points=[CapacitySamplePoint(sampled_at=r.sampled_at, capacity_bytes=r.capacity_bytes, used_bytes=r.used_bytes) for r in rows],
    )


@router.get("", response_model=list[CapacitySeries])
def get_capacity_history(
    object_type: CapacityObjectType,
    cluster_id: str = Query(...),
    months: int = Query(3, ge=1, le=12),
    vm_uuid: str | None = None,
    name: str | None = None,
    uuid: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CapacitySeries]:
    _require_view(object_type, user, db)
    since = datetime.now(timezone.utc) - timedelta(days=months * 30)

    if object_type == "vhd":
        if not vm_uuid:
            raise HTTPException(status_code=400, detail="vm_uuid ist fuer object_type=vhd erforderlich")
        vhds = db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id, HyperVVhd.vm_uuid == vm_uuid).all()
        return [
            _series_for_key(
                db, "vhd", capacity_key("vhd", cluster_id, path=vhd.path), (vhd.path or "").split("\\")[-1] or vhd.path, since
            )
            for vhd in vhds
        ]

    if object_type == "csv":
        if not name:
            raise HTTPException(status_code=400, detail="name ist fuer object_type=csv erforderlich")
        key = capacity_key("csv", cluster_id, name=name)
        return [_series_for_key(db, "csv", key, name, since)]

    if object_type in ("lun", "volume", "aggregate"):
        model = {"lun": NetAppLun, "volume": NetAppVolume, "aggregate": NetAppAggregate}[object_type]
        obj = None
        if uuid:
            obj = db.query(model).filter(model.cluster_id == cluster_id, model.uuid == uuid).first()
        elif name:
            obj = db.query(model).filter(model.cluster_id == cluster_id, model.name == name).first()
        resolved_name = obj.name if obj else (name or uuid or "")
        key = capacity_key(object_type, cluster_id, uuid=uuid, name=resolved_name)
        return [_series_for_key(db, object_type, key, resolved_name, since)]

    raise HTTPException(status_code=400, detail=f"Unbekannter object_type: {object_type}")
