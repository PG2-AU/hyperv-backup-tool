"""Hyper-V-Failover-Cluster-Verwaltung: Hinzufuegen/Entfernen registrierter
Cluster sowie Verbindungstest per WinRM (siehe HyperVService.get_cluster_summary).

Registriert wird der Cluster (Cluster Name Object / Management-IP), nicht die
einzelnen Knoten -- vgl. NetApp-Cluster-Verwaltung in netapp_clusters.py.
"""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.hyperv_cluster import HyperVCluster, HyperVClusterHealth
from app.models.hyperv_discovery import HyperVVhd, HyperVVm
from app.schemas.hyperv_cluster import HyperVClusterCreate, HyperVClusterRead, HyperVReachabilityCheck
from app.schemas.netapp_cluster import DiscoveryStepRead
from app.services.hyperv_service import HyperVConnectionError, HyperVService, check_reachability

router = APIRouter(prefix="/api/hyperv/clusters", tags=["hyperv-clusters"])

_CSV_NAME_RE = re.compile(r"ClusterStorage\\([^\\]+)\\", re.IGNORECASE)


def _parse_csv_name(vhd_path: str) -> str | None:
    match = _CSV_NAME_RE.search(vhd_path)
    return match.group(1) if match else None


def _get_cluster_or_404(db: Session, cluster_id: str) -> HyperVCluster:
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    return cluster


def _service_for(cluster: HyperVCluster) -> HyperVService:
    return HyperVService(get_settings(), cluster.management_address, use_https=cluster.use_https)


def _apply_summary(cluster: HyperVCluster, summary) -> None:
    cluster.hyperv_cluster_name = summary.cluster_name
    cluster.node_count = summary.node_count
    cluster.healthy_node_count = summary.healthy_node_count
    cluster.health = (
        HyperVClusterHealth.HEALTHY
        if summary.node_count > 0 and summary.healthy_node_count == summary.node_count
        else HyperVClusterHealth.DEGRADED
    )
    cluster.last_check_error = None


def _refresh_status(db: Session, cluster: HyperVCluster) -> HyperVCluster:
    service = _service_for(cluster)
    try:
        summary = service.get_cluster_summary(cluster.username, decrypt_secret(cluster.encrypted_password))
        _apply_summary(cluster, summary)
    except HyperVConnectionError as exc:
        cluster.health = HyperVClusterHealth.UNREACHABLE
        cluster.last_check_error = str(exc)
    cluster.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.get("", response_model=list[HyperVClusterRead])
def list_clusters(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[HyperVCluster]:
    return db.query(HyperVCluster).order_by(HyperVCluster.name).all()


@router.post("/check-reachability")
def check_reachability_route(
    payload: HyperVReachabilityCheck, user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> dict:
    port = 5986 if payload.use_https else 5985
    try:
        check_reachability(payload.management_address, port)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "reachable"}


@router.post("", response_model=HyperVClusterRead, status_code=status.HTTP_201_CREATED)
def create_cluster(
    payload: HyperVClusterCreate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> HyperVCluster:
    if db.query(HyperVCluster).filter(HyperVCluster.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Cluster mit diesem Namen existiert bereits")

    probe = HyperVService(get_settings(), payload.management_address, use_https=payload.use_https)
    try:
        summary = probe.get_cluster_summary(payload.username, payload.password)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Verbindung fehlgeschlagen: {exc}") from exc

    cluster = HyperVCluster(
        name=payload.name,
        management_address=payload.management_address,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        use_https=payload.use_https,
        last_checked_at=datetime.now(timezone.utc),
    )
    _apply_summary(cluster, summary)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.post("/{cluster_id}/verify", response_model=HyperVClusterRead)
def verify_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> HyperVCluster:
    cluster = _get_cluster_or_404(db, cluster_id)
    return _refresh_status(db, cluster)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)
    db.delete(cluster)
    db.commit()


@router.post("/{cluster_id}/discover", response_model=list[DiscoveryStepRead])
def discover_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
):
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    steps, data = service.run_vm_discovery(cluster.username, decrypt_secret(cluster.encrypted_password))

    # Nur ersetzen, wenn mindestens ein Knoten erfolgreich abgefragt wurde --
    # sonst wuerde ein voruebergehend nicht erreichbarer Cluster die bereits
    # bekannten VMs faelschlich loeschen (analog zur NetApp-Discovery).
    if any(s.success for s in steps if s.step == "vms"):
        now = datetime.now(timezone.utc)
        db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster.id).delete()
        db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster.id).delete()
        for vm in data.vms:
            db.add(
                HyperVVm(
                    cluster_id=cluster.id, vm_uuid=vm.id, name=vm.name, state=vm.state, host_name=vm.host, last_seen_at=now,
                )
            )
            for vhd in vm.vhds:
                db.add(
                    HyperVVhd(
                        cluster_id=cluster.id, vm_uuid=vm.id, vm_name=vm.name, path=vhd.path,
                        csv_name=_parse_csv_name(vhd.path), size_bytes=vhd.size_bytes, used_bytes=vhd.used_bytes,
                        last_seen_at=now,
                    )
                )
        db.commit()

    return steps
