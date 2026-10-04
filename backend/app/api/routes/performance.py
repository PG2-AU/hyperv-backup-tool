"""Performance (Backlog #80 Stufe 1, Storage-Seite): IOPS, Latenz und
Durchsatz der CSVs (ueber ihre LUN) und SMB3-Freigaben (ueber ihr Volume)
sowie aller Volumes -- live von ONTAP, Verlauf aus ONTAPs eigener Historie
(/metrics, bis 1 Jahr). Die App sammelt dafuer nichts selbst.

GET /api/performance/overview: aktuelle Werte (15-s-Mittel) je CSV, SMB3-
Freigabe und Volume. Ein REST-Aufruf je NetApp-System fuer alle Volumes und
einer fuer alle LUNs, 20 s zwischengespeichert (Auto-Refresh mehrerer
Browser). GET /api/performance/history: Verlauf eines Volumes/einer LUN.

Zuordnung CSV -> LUN ueber System-Name + SVM + LUN-Pfad aus list_csvs (dieselbe
Zuordnung wie in Inventory), UUID aus der NetApp-Discovery."""

import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppLun, NetAppVolume
from app.services.netapp_service import NetAppConnectionError, netapp_service_for_cluster

router = APIRouter(prefix="/api/performance", tags=["performance"])

_view = require_permission(Permission.STORAGE_VIEW)
CACHE_SECONDS = 20
HISTORY_CACHE_SECONDS = 60

_lock = threading.Lock()
_current_cache: dict[str, tuple[float, dict]] = {}
_history_cache: dict[tuple, tuple[float, list]] = {}


class Rwt(BaseModel):
    read: float | None = None
    write: float | None = None
    total: float | None = None


class PerfValues(BaseModel):
    iops: Rwt
    latency_ms: Rwt
    throughput: Rwt  # Bytes/s
    timestamp: str | None = None
    status: str | None = None


class PerfRow(BaseModel):
    kind: Literal["csv", "smb_share", "volume"]
    name: str
    hyperv_cluster_name: str | None = None
    netapp_cluster_id: str | None = None
    netapp_cluster_name: str | None = None
    svm_name: str | None = None
    object_type: Literal["lun", "volume"] | None = None
    object_uuid: str | None = None
    object_name: str | None = None
    values: PerfValues | None = None
    # Warum keine Werte (System nicht erreichbar, keine Zuordnung, ...)
    note: str | None = None


class PerfOverview(BaseModel):
    rows: list[PerfRow]
    errors: list[str]


class PerfPoint(BaseModel):
    timestamp: str
    iops_read: float | None = None
    iops_write: float | None = None
    iops_total: float | None = None
    latency_read_ms: float | None = None
    latency_write_ms: float | None = None
    latency_total_ms: float | None = None
    throughput_read: float | None = None
    throughput_write: float | None = None
    throughput_total: float | None = None


def _rwt(values: dict | None, factor: float = 1.0) -> Rwt:
    values = values or {}
    return Rwt(**{k: (values[k] * factor if values.get(k) is not None else None) for k in ("read", "write", "total")})


def _values(metric: dict) -> PerfValues:
    return PerfValues(
        iops=_rwt(metric.get("iops")), latency_ms=_rwt(metric.get("latency"), 0.001), throughput=_rwt(metric.get("throughput")),
        timestamp=metric.get("timestamp"), status=metric.get("status"),
    )


def _current(cluster: NetAppCluster) -> dict:
    now = time.monotonic()
    with _lock:
        cached = _current_cache.get(cluster.id)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    data = netapp_service_for_cluster(cluster).current_metrics()
    with _lock:
        _current_cache[cluster.id] = (now, data)
    return data


@router.get("/overview", response_model=PerfOverview)
def overview(db: Session = Depends(get_db), user=Depends(_view)) -> PerfOverview:
    from app.api.routes.vms import list_csvs, list_smb_shares

    clusters = db.query(NetAppCluster).all()
    by_name = {c.name: c for c in clusters}
    metrics: dict[str, dict] = {}
    errors: list[str] = []
    for cluster in clusters:
        try:
            metrics[cluster.id] = _current(cluster)
        except NetAppConnectionError as exc:
            errors.append(f"{cluster.name}: {exc}")

    luns = {(l.cluster_id, l.svm_name, l.name): l for l in db.query(NetAppLun).all()}
    volumes = db.query(NetAppVolume).all()
    volumes_by_key = {(v.cluster_id, v.svm_name, v.name): v for v in volumes}
    rows: list[PerfRow] = []

    def lookup(cluster: NetAppCluster | None, object_type: str, uuid: str | None) -> tuple[PerfValues | None, str | None]:
        if cluster is None:
            return None, "Kein NetApp-System zugeordnet"
        if cluster.id not in metrics:
            return None, "NetApp-System nicht erreichbar"
        if not uuid:
            return None, "Objekt nicht in der NetApp-Discovery"
        entry = metrics[cluster.id][object_type].get(uuid)
        return (_values(entry["metric"]), None) if entry else (None, "Keine Messwerte (z.B. offline)")

    for csv in list_csvs(db, None):
        cluster = by_name.get(csv.netapp_cluster_name or "")
        lun = luns.get((cluster.id, csv.svm_name, csv.lun_name)) if cluster and csv.lun_name else None
        values, note = lookup(cluster, "lun", lun.uuid if lun else None)
        rows.append(PerfRow(
            kind="csv", name=csv.name, hyperv_cluster_name=csv.hyperv_cluster_name, netapp_cluster_id=cluster.id if cluster else None,
            netapp_cluster_name=csv.netapp_cluster_name, svm_name=csv.svm_name, object_type="lun", object_uuid=lun.uuid if lun else None,
            object_name=csv.lun_name, values=values, note=note,
        ))
    for share in list_smb_shares(db, None):
        cluster = by_name.get(share.netapp_cluster_name or "")
        volume = volumes_by_key.get((cluster.id, share.svm_name, share.volume_name)) if cluster else None
        values, note = lookup(cluster, "volume", volume.uuid if volume else None)
        rows.append(PerfRow(
            kind="smb_share", name=f"\\\\{share.server}\\{share.share}", hyperv_cluster_name=share.hyperv_cluster_name,
            netapp_cluster_id=cluster.id if cluster else None, netapp_cluster_name=share.netapp_cluster_name, svm_name=share.svm_name,
            object_type="volume", object_uuid=volume.uuid if volume else None, object_name=share.volume_name, values=values, note=note,
        ))
    by_id = {c.id: c for c in clusters}
    for volume in volumes:
        cluster = by_id.get(volume.cluster_id)
        values, note = lookup(cluster, "volume", volume.uuid)
        if values is None and volume.cluster_id in metrics:
            continue  # offline/DP-Volumes ohne Messwerte nicht auflisten
        rows.append(PerfRow(
            kind="volume", name=volume.name, netapp_cluster_id=volume.cluster_id, netapp_cluster_name=cluster.name if cluster else None,
            svm_name=volume.svm_name, object_type="volume", object_uuid=volume.uuid, object_name=volume.name, values=values, note=note,
        ))
    return PerfOverview(rows=rows, errors=errors)


@router.get("/history", response_model=list[PerfPoint])
def history(
    netapp_cluster_id: str,
    object_type: Literal["lun", "volume"],
    uuid: str,
    interval: Literal["1h", "1d", "1w", "1m", "1y"] = "1d",
    db: Session = Depends(get_db),
    user=Depends(_view),
) -> list[PerfPoint]:
    cluster = db.get(NetAppCluster, netapp_cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetApp-System nicht gefunden")
    key = (netapp_cluster_id, object_type, uuid, interval)
    now = time.monotonic()
    with _lock:
        cached = _history_cache.get(key)
    if cached and now - cached[0] < HISTORY_CACHE_SECONDS:
        records = cached[1]
    else:
        try:
            records = netapp_service_for_cluster(cluster).metrics_history(object_type, uuid, interval)
        except NetAppConnectionError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        with _lock:
            _history_cache[key] = (now, records)
            for stale in [k for k, v in _history_cache.items() if now - v[0] > HISTORY_CACHE_SECONDS * 5]:
                _history_cache.pop(stale, None)
    points = []
    for r in records:
        if not r.get("timestamp"):
            continue
        iops, latency, throughput = r.get("iops") or {}, r.get("latency") or {}, r.get("throughput") or {}
        points.append(PerfPoint(
            timestamp=r["timestamp"],
            iops_read=iops.get("read"), iops_write=iops.get("write"), iops_total=iops.get("total"),
            latency_read_ms=latency["read"] / 1000 if latency.get("read") is not None else None,
            latency_write_ms=latency["write"] / 1000 if latency.get("write") is not None else None,
            latency_total_ms=latency["total"] / 1000 if latency.get("total") is not None else None,
            throughput_read=throughput.get("read"), throughput_write=throughput.get("write"), throughput_total=throughput.get("total"),
        ))
    return points

