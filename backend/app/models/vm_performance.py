"""VM-Performance-Messpunkte (Backlog #80 Stufe 2) aus Storage QoS des
Failover-Clusters -- je VM und CSV (alle virtuellen Disks einer VM auf
derselben CSV zusammengefasst). Anders als die Storage-Seite (ONTAP haelt den
Verlauf selbst vor) speichert Hyper-V keine Historie, die App sammelt daher
selbst, siehe app.core.vm_performance.

resolution "raw": ein Punkt je Sammellauf (Standard alle 5 Minuten), 7 Tage
aufbewahrt; danach zu "1h" (Stundenmittel) verdichtet und 90 Tage behalten."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VmPerfSample(Base):
    __tablename__ = "vm_perf_samples"
    __table_args__ = (
        Index("ix_vm_perf_vm_time", "cluster_id", "vm_id", "sampled_at"),
        Index("ix_vm_perf_time", "sampled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36))  # Hyper-V-Cluster
    vm_id: Mapped[str] = mapped_column(String(64))
    vm_name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    csv_name: Mapped[str] = mapped_column(String(255))
    resolution: Mapped[str] = mapped_column(String(8), default="raw")  # raw | 1h
    sampled_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    iops: Mapped[float] = mapped_column(Float, default=0.0)  # normalisiert (8 KB)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)  # IOPS-gewichtetes Mittel der Disks
    bandwidth: Mapped[float] = mapped_column(Float, default=0.0)  # Bytes/s
    disk_count: Mapped[int] = mapped_column(Integer, default=1)
