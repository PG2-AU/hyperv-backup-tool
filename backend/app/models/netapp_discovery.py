"""Persistierte Ergebnisse der NetApp-Cluster-Discovery (SVMs, Volumes, LUNs,
Cluster-/SVM-Peers, SnapMirror-Beziehungen, Network Interfaces, Plattformen,
Aggregate). Pro Cluster wird bei jedem Discovery-Lauf je Objekttyp geloescht
und neu eingefuegt (Replace-Strategie), 'last_seen_at' markiert den Zeitpunkt
des letzten erfolgreichen Laufs, in dem das Objekt gefunden wurde."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
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
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NetAppClusterPeer(Base):
    __tablename__ = "netapp_cluster_peers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remote_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
