"""Persistierte Ergebnisse der Hyper-V-Cluster-Discovery (VMs inkl. ihrer
VHDs). Pro Cluster wird bei jedem Discovery-Lauf, der mindestens einen
erreichbaren Knoten hatte, die komplette VM-/VHD-Liste ersetzt (Replace-
Strategie), analog zur NetApp-Discovery in netapp_discovery.py."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HyperVVm(Base):
    __tablename__ = "hyperv_vms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    host_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVVhd(Base):
    __tablename__ = "hyperv_vhds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    path: Mapped[str] = mapped_column(String(1000))
    csv_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
