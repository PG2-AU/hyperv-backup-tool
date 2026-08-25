"""NetApp-Storage-Uebersicht: SVMs, Volumes, LUNs, Cluster-/SVM-Peers,
SnapMirror-Beziehungen, Network Interfaces, Plattformen und Aggregate --
DB-backed aus den Ergebnissen der Cluster-Discovery (siehe netapp_clusters.py).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import (
    NetAppAggregate,
    NetAppClusterPeer,
    NetAppIgroup,
    NetAppLun,
    NetAppNetworkInterface,
    NetAppPlatform,
    NetAppSchedule,
    NetAppSnapMirrorPolicy,
    NetAppSnapMirrorRelationship,
    NetAppSvm,
    NetAppSvmPeer,
    NetAppVolume,
)
from app.schemas.netapp_discovery import (
    NetAppAggregateRead,
    NetAppClusterPeerRead,
    NetAppIgroupRead,
    NetAppLunRead,
    NetAppNetworkInterfaceRead,
    NetAppPlatformRead,
    NetAppScheduleRead,
    NetAppSnapMirrorPolicyRead,
    NetAppSnapMirrorRelationshipRead,
    NetAppSvmPeerRead,
    NetAppSvmRead,
    NetAppVolumeRead,
)
from app.schemas.storage import MetroClusterStatus

router = APIRouter(prefix="/api/storage", tags=["storage"])


def _cluster_names(db: Session) -> dict[str, str]:
    return {c.id: c.name for c in db.query(NetAppCluster).all()}


@router.get("/svms", response_model=list[NetAppSvmRead])
def list_svms(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppSvmRead]:
    names = _cluster_names(db)
    return [
        NetAppSvmRead(
            id=s.id, cluster_id=s.cluster_id, cluster_name=names.get(s.cluster_id, "?"),
            uuid=s.uuid, name=s.name, state=s.state, subtype=s.subtype,
            allowed_protocols=s.allowed_protocols, data_services=s.data_services, last_seen_at=s.last_seen_at,
        )
        for s in db.query(NetAppSvm).order_by(NetAppSvm.name).all()
    ]


@router.get("/volumes", response_model=list[NetAppVolumeRead])
def list_volumes(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppVolumeRead]:
    names = _cluster_names(db)
    return [
        NetAppVolumeRead(
            id=v.id, cluster_id=v.cluster_id, cluster_name=names.get(v.cluster_id, "?"),
            uuid=v.uuid, name=v.name, svm_name=v.svm_name, state=v.state,
            size_bytes=v.size_bytes, used_bytes=v.used_bytes, percent_used=v.percent_used,
            security_style=v.security_style, language=v.language,
            snapshot_autodelete_enabled=v.snapshot_autodelete_enabled, autosize_mode=v.autosize_mode,
            snapshot_policy_name=v.snapshot_policy_name, encryption_enabled=v.encryption_enabled,
            snapmirror_protected=v.snapmirror_protected, last_seen_at=v.last_seen_at,
        )
        for v in db.query(NetAppVolume).order_by(NetAppVolume.name).all()
    ]


@router.get("/luns", response_model=list[NetAppLunRead])
def list_luns(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppLunRead]:
    names = _cluster_names(db)
    return [
        NetAppLunRead(
            id=l.id, cluster_id=l.cluster_id, cluster_name=names.get(l.cluster_id, "?"),
            uuid=l.uuid, name=l.name, svm_name=l.svm_name, volume_name=l.volume_name,
            state=l.state, size_bytes=l.size_bytes, os_type=l.os_type, mapped_igroups=l.mapped_igroups,
            last_seen_at=l.last_seen_at,
        )
        for l in db.query(NetAppLun).order_by(NetAppLun.name).all()
    ]


@router.get("/igroups", response_model=list[NetAppIgroupRead])
def list_igroups(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppIgroupRead]:
    names = _cluster_names(db)
    return [
        NetAppIgroupRead(
            id=ig.id, cluster_id=ig.cluster_id, cluster_name=names.get(ig.cluster_id, "?"),
            uuid=ig.uuid, name=ig.name, svm_name=ig.svm_name, os_type=ig.os_type,
            protocol=ig.protocol, initiator_count=ig.initiator_count, last_seen_at=ig.last_seen_at,
        )
        for ig in db.query(NetAppIgroup).order_by(NetAppIgroup.name).all()
    ]


@router.get("/cluster-peers", response_model=list[NetAppClusterPeerRead])
def list_cluster_peers(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppClusterPeerRead]:
    names = _cluster_names(db)
    return [
        NetAppClusterPeerRead(
            id=p.id, cluster_id=p.cluster_id, cluster_name=names.get(p.cluster_id, "?"),
            uuid=p.uuid, name=p.name, remote_name=p.remote_name, state=p.state,
            peer_ip_addresses=p.peer_ip_addresses, local_ip_addresses=p.local_ip_addresses,
            last_seen_at=p.last_seen_at,
        )
        for p in db.query(NetAppClusterPeer).all()
    ]


@router.get("/svm-peers", response_model=list[NetAppSvmPeerRead])
def list_svm_peers(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppSvmPeerRead]:
    names = _cluster_names(db)
    return [
        NetAppSvmPeerRead(
            id=p.id, cluster_id=p.cluster_id, cluster_name=names.get(p.cluster_id, "?"),
            uuid=p.uuid, svm_name=p.svm_name, peer_svm_name=p.peer_svm_name,
            peer_cluster_name=p.peer_cluster_name, state=p.state, applications=p.applications,
            last_seen_at=p.last_seen_at,
        )
        for p in db.query(NetAppSvmPeer).all()
    ]


@router.get("/snapmirror-relationships", response_model=list[NetAppSnapMirrorRelationshipRead])
def list_snapmirror_relationships(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))
) -> list[NetAppSnapMirrorRelationshipRead]:
    names = _cluster_names(db)
    return [
        NetAppSnapMirrorRelationshipRead(
            id=r.id, cluster_id=r.cluster_id, cluster_name=names.get(r.cluster_id, "?"),
            uuid=r.uuid, source_path=r.source_path, destination_path=r.destination_path,
            state=r.state, healthy=r.healthy, lag_time=r.lag_time,
            last_transfer_size_bytes=r.last_transfer_size_bytes, last_transfer_error=r.last_transfer_error,
            schedule_name=r.schedule_name, policy_name=r.policy_name,
            destination_cluster_name=r.destination_cluster_name, last_seen_at=r.last_seen_at,
        )
        for r in db.query(NetAppSnapMirrorRelationship).all()
    ]


@router.get("/network-interfaces", response_model=list[NetAppNetworkInterfaceRead])
def list_network_interfaces(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))
) -> list[NetAppNetworkInterfaceRead]:
    names = _cluster_names(db)
    return [
        NetAppNetworkInterfaceRead(
            id=i.id, cluster_id=i.cluster_id, cluster_name=names.get(i.cluster_id, "?"),
            uuid=i.uuid, name=i.name, address=i.address, svm_name=i.svm_name, state=i.state, last_seen_at=i.last_seen_at,
        )
        for i in db.query(NetAppNetworkInterface).order_by(NetAppNetworkInterface.name).all()
    ]


@router.get("/platforms", response_model=list[NetAppPlatformRead])
def list_platforms(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppPlatformRead]:
    names = _cluster_names(db)
    return [
        NetAppPlatformRead(
            id=p.id, cluster_id=p.cluster_id, cluster_name=names.get(p.cluster_id, "?"),
            uuid=p.uuid, node_name=p.node_name, model=p.model, serial_number=p.serial_number,
            ontap_version=p.ontap_version, uptime_seconds=p.uptime_seconds, state=p.state, last_seen_at=p.last_seen_at,
        )
        for p in db.query(NetAppPlatform).order_by(NetAppPlatform.node_name).all()
    ]


@router.get("/aggregates", response_model=list[NetAppAggregateRead])
def list_aggregates(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppAggregateRead]:
    names = _cluster_names(db)
    return [
        NetAppAggregateRead(
            id=a.id, cluster_id=a.cluster_id, cluster_name=names.get(a.cluster_id, "?"),
            uuid=a.uuid, name=a.name, node_name=a.node_name, state=a.state,
            size_bytes=a.size_bytes, used_bytes=a.used_bytes, used_percent=a.used_percent,
            efficiency_ratio=a.efficiency_ratio, efficiency_ratio_wo_snapshots=a.efficiency_ratio_wo_snapshots,
            last_seen_at=a.last_seen_at,
        )
        for a in db.query(NetAppAggregate).order_by(NetAppAggregate.name).all()
    ]


@router.get("/snapmirror-policies", response_model=list[NetAppSnapMirrorPolicyRead])
def list_snapmirror_policies(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))
) -> list[NetAppSnapMirrorPolicyRead]:
    names = _cluster_names(db)
    return [
        NetAppSnapMirrorPolicyRead.from_model(p, names.get(p.cluster_id, "?"))
        for p in db.query(NetAppSnapMirrorPolicy).order_by(NetAppSnapMirrorPolicy.name).all()
    ]


@router.get("/schedules", response_model=list[NetAppScheduleRead])
def list_schedules(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppScheduleRead]:
    names = _cluster_names(db)
    return [
        NetAppScheduleRead.from_model(s, names.get(s.cluster_id, "?"))
        for s in db.query(NetAppSchedule).order_by(NetAppSchedule.name).all()
    ]


@router.get("/metrocluster-status", response_model=MetroClusterStatus)
def metrocluster_status(user=Depends(require_permission(Permission.STORAGE_VIEW))) -> MetroClusterStatus:
    return MetroClusterStatus(configured=True, mode="normal", switchover_in_progress=False)
