"""Persistierte Ergebnisse der NetApp-Cluster-Discovery (SVMs, Volumes, LUNs,
Cluster-/SVM-Peers, SnapMirror-Beziehungen, Network Interfaces, Plattformen,
Aggregate). Pro Cluster wird bei jedem Discovery-Lauf je Objekttyp geloescht
und neu eingefuegt (Replace-Strategie), 'last_seen_at' markiert den Zeitpunkt
des letzten erfolgreichen Laufs, in dem das Objekt gefunden wurde."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NetAppSvm(Base):
    __tablename__ = "netapp_svms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subtype: Mapped[str | None] = mapped_column(String(50), nullable=True)
    allowed_protocols: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_services: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppVolume(Base):
    __tablename__ = "netapp_volumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percent_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    security_style: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    snapshot_autodelete_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    autosize_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    snapshot_policy_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encryption_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    snapmirror_protected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppLun(Base):
    __tablename__ = "netapp_luns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(500))
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    os_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mapped_igroups: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppIgroup(Base):
    __tablename__ = "netapp_igroups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    initiator_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppClusterPeer(Base):
    __tablename__ = "netapp_cluster_peers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remote_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    peer_ip_addresses: Mapped[str | None] = mapped_column(String(500), nullable=True)
    local_ip_addresses: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppSvmPeer(Base):
    __tablename__ = "netapp_svm_peers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    peer_svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    peer_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    applications: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppSnapMirrorRelationship(Base):
    __tablename__ = "netapp_snapmirror_relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    destination_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=False)
    lag_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_transfer_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_transfer_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    schedule_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppNetworkInterface(Base):
    __tablename__ = "netapp_network_interfaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppSnapMirrorPolicy(Base):
    __tablename__ = "netapp_snapmirror_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(20), nullable=True)
    type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rules_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppSchedule(Base):
    __tablename__ = "netapp_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(20), nullable=True)
    schedule_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    minutes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hours: Mapped[str | None] = mapped_column(String(255), nullable=True)
    days: Mapped[str | None] = mapped_column(String(255), nullable=True)
    weekdays: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppPlatform(Base):
    __tablename__ = "netapp_platforms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    node_name: Mapped[str] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ontap_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uptime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppAggregate(Base):
    __tablename__ = "netapp_aggregates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    node_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    efficiency_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    efficiency_ratio_wo_snapshots: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
