"""Persistierte Laeufe "CSV vergroessern" (Backlog #69): optional Volume,
optional LUN auf der NetApp vergroessern, Datentraeger auf allen Knoten neu
einlesen, Partition auf dem Owner-Knoten erweitern. Hintergrund-Task mit
Schritt-Protokoll wie VmMoveRun, siehe app.api.routes.csv_resize."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CsvResizeRun(Base):
    __tablename__ = "csv_resize_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    csv_name: Mapped[str] = mapped_column(String(255))
    disk_serial_number: Mapped[str] = mapped_column(String(100))
    netapp_cluster_id: Mapped[str] = mapped_column(String(36))
    # None = nicht aendern (z.B. nur Partition erweitern).
    new_volume_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_lun_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csv_size_before_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csv_size_after_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "CsvResizeRunStep", back_populates="run", cascade="all, delete-orphan", order_by="CsvResizeRunStep.created_at",
    )


class CsvResizeRunStep(Base):
    __tablename__ = "csv_resize_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("csv_resize_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("CsvResizeRun", back_populates="steps")
