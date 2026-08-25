from datetime import datetime

from pydantic import BaseModel


class NetAppSvmRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    state: str | None = None
    subtype: str | None = None
    last_seen_at: datetime


class NetAppVolumeRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    svm_name: str | None = None
    state: str | None = None
    size_bytes: int | None = None
    used_bytes: int | None = None
    last_seen_at: datetime


class NetAppLunRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    svm_name: str | None = None
    volume_name: str | None = None
    state: str | None = None
    size_bytes: int | None = None
    os_type: str | None = None
    last_seen_at: datetime


class NetAppClusterPeerRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str | None = None
    remote_name: str | None = None
    state: str | None = None
    last_seen_at: datetime


class NetAppSvmPeerRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    svm_name: str | None = None
    peer_svm_name: str | None = None
    peer_cluster_name: str | None = None
    state: str | None = None
    last_seen_at: datetime


class NetAppSnapMirrorRelationshipRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    source_path: str | None = None
    destination_path: str | None = None
    state: str | None = None
    healthy: bool
    last_seen_at: datetime


class NetAppNetworkInterfaceRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str | None = None
    address: str | None = None
    svm_name: str | None = None
    state: str | None = None
    last_seen_at: datetime


class NetAppPlatformRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    node_name: str
    model: str | None = None
    serial_number: str | None = None
    ontap_version: str | None = None
    uptime_seconds: int | None = None
    state: str | None = None
    last_seen_at: datetime


class NetAppAggregateRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    node_name: str | None = None
    state: str | None = None
    size_bytes: int | None = None
    used_bytes: int | None = None
    last_seen_at: datetime
