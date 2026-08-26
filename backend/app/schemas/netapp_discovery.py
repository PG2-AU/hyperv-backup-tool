import json
from datetime import datetime

from pydantic import BaseModel


class SnapMirrorPolicyRuleInfo(BaseModel):
    label: str
    count: str


class NetAppSnapMirrorPolicyRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    svm_name: str | None = None
    scope: str | None = None
    type: str | None = None
    comment: str | None = None
    rules: list[SnapMirrorPolicyRuleInfo] = []
    last_seen_at: datetime

    @classmethod
    def from_model(cls, m, cluster_name: str) -> "NetAppSnapMirrorPolicyRead":
        try:
            rules = [SnapMirrorPolicyRuleInfo(**r) for r in json.loads(m.rules_json or "[]")]
        except (json.JSONDecodeError, TypeError):
            rules = []
        return cls(
            id=m.id, cluster_id=m.cluster_id, cluster_name=cluster_name, uuid=m.uuid, name=m.name,
            svm_name=m.svm_name, scope=m.scope, type=m.type, comment=m.comment, rules=rules, last_seen_at=m.last_seen_at,
        )


class NetAppScheduleRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    svm_name: str | None = None
    scope: str | None = None
    schedule_type: str | None = None
    minutes: list[int] = []
    hours: list[int] = []
    days: list[int] = []
    weekdays: list[int] = []
    last_seen_at: datetime

    @classmethod
    def from_model(cls, m, cluster_name: str) -> "NetAppScheduleRead":
        def parse(s: str | None) -> list[int]:
            return [int(x) for x in s.split(",")] if s else []

        return cls(
            id=m.id, cluster_id=m.cluster_id, cluster_name=cluster_name, uuid=m.uuid, name=m.name,
            svm_name=m.svm_name, scope=m.scope, schedule_type=m.schedule_type,
            minutes=parse(m.minutes), hours=parse(m.hours), days=parse(m.days), weekdays=parse(m.weekdays),
            last_seen_at=m.last_seen_at,
        )


class NetAppSvmRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    state: str | None = None
    subtype: str | None = None
    allowed_protocols: str | None = None
    data_services: str | None = None
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
    percent_used: int | None = None
    security_style: str | None = None
    language: str | None = None
    snapshot_autodelete_enabled: bool | None = None
    autosize_mode: str | None = None
    snapshot_policy_name: str | None = None
    encryption_enabled: bool | None = None
    snapmirror_protected: bool | None = None
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
    mapped_igroups: str | None = None
    serial_number: str | None = None
    last_seen_at: datetime


class NetAppIgroupRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    svm_name: str | None = None
    os_type: str | None = None
    protocol: str | None = None
    initiator_count: int
    last_seen_at: datetime


class NetAppClusterPeerRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str | None = None
    remote_name: str | None = None
    state: str | None = None
    peer_ip_addresses: str | None = None
    local_ip_addresses: str | None = None
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
    applications: str | None = None
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
    lag_time: str | None = None
    last_transfer_size_bytes: int | None = None
    last_transfer_error: str | None = None
    schedule_name: str | None = None
    policy_name: str | None = None
    destination_cluster_name: str | None = None
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
    used_percent: int | None = None
    efficiency_ratio: float | None = None
    efficiency_ratio_wo_snapshots: float | None = None
    last_seen_at: datetime
