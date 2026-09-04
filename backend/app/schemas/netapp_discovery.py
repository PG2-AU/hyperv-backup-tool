import json
from datetime import datetime

from pydantic import BaseModel


class SnapMirrorPolicyRuleInfo(BaseModel):
    label: str
    count: str


def _derive_snapmirror_policy_display_type(
    type_: str | None, create_snapshot_on_source: bool | None, sync_type: str | None, rules: list[dict],
) -> str | None:
    """Die ONTAP-CLI ('vserver snapmirror policy show') zeigt eine feinere
    Kategorie an (vault/mirror-vault/async-mirror/sync-mirror/
    strict-sync-mirror) als der rohe REST-Typ, der nur async/sync/
    continuous kennt. Live gegen eine echte Policy verifiziert: eine reine
    Vault-Policy (create_snapshot_on_source=False) hat REST-type='async',
    die CLI zeigt aber 'vault' -- ohne diese Ableitung zeigte unsere GUI
    faelschlich immer den rohen REST-Typ ("async") an, egal ob Vault oder
    Mirror-Vault."""
    if type_ is None:
        return None
    if type_ == "sync":
        # ONTAP liefert hier 'sync', 'strict_sync' oder 'automated_failover'
        # (live verifiziert) -- NICHT 'sync_mirror'/'strict_sync_mirror',
        # wie man von den CLI-Bezeichnungen erwarten wuerde.
        if sync_type == "strict_sync":
            return "strict_sync_mirror"
        if sync_type == "automated_failover":
            return "automated_failover_sync"
        return "sync_mirror"
    if type_ == "async":
        if create_snapshot_on_source is False:
            return "vault"
        # create_snapshot_on_source True oder (ONTAP-Default) gar nicht gesetzt:
        # reines Mirroring ("all_source_snapshots"-Regel ohne weitere
        # Retention-Regeln) vs. Mirror-and-Vault (zusaetzliche
        # Zeitplan-Retention-Regeln neben dem Mirror).
        other_rules = [r for r in rules if r.get("label") != "all_source_snapshots"]
        return "async_mirror" if not other_rules else "mirror_vault"
    return type_


class NetAppSnapMirrorPolicyRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str
    uuid: str | None = None
    name: str
    svm_name: str | None = None
    scope: str | None = None
    type: str | None = None
    display_type: str | None = None
    create_snapshot_on_source: bool | None = None
    comment: str | None = None
    rules: list[SnapMirrorPolicyRuleInfo] = []
    last_seen_at: datetime

    @classmethod
    def from_model(cls, m, cluster_name: str) -> "NetAppSnapMirrorPolicyRead":
        try:
            raw_rules = json.loads(m.rules_json or "[]")
        except (json.JSONDecodeError, TypeError):
            raw_rules = []
        try:
            rules = [SnapMirrorPolicyRuleInfo(**r) for r in raw_rules]
        except (TypeError, ValueError):
            rules = []
        return cls(
            id=m.id, cluster_id=m.cluster_id, cluster_name=cluster_name, uuid=m.uuid, name=m.name,
            svm_name=m.svm_name, scope=m.scope, type=m.type,
            display_type=_derive_snapmirror_policy_display_type(m.type, m.create_snapshot_on_source, m.sync_type, raw_rules),
            create_snapshot_on_source=m.create_snapshot_on_source,
            comment=m.comment, rules=rules, last_seen_at=m.last_seen_at,
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
    used_bytes: int | None = None
    # Anders als bei NetAppVolume (space.percent_used direkt von ONTAP) gibt
    # es fuer LUNs keinen eigenen ONTAP-Prozentwert -- wird aus used_bytes/
    # size_bytes berechnet (siehe storage.py, gleiche Formel wie im
    # Kapazitaets-Alarm-Check in scheduler.py).
    percent_used: int | None = None
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
