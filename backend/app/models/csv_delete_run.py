"""Persistierte Laeufe "CSV loeschen": CSV und Cluster-Disk entfernen, LUN-
Mapping aufheben, LUN und (falls einzige LUN) Volume auf der NetApp loeschen.
Hintergrund-Task mit Schritt-Protokoll wie CsvCreateRun, siehe
app.api.routes.csv_delete. Kein Zurueckrollen -- das Loeschen ist endgueltig."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CsvDeleteRun(Base):
    __tablename__ = "csv_delete_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    csv_name: Mapped[str] = mapped_column(String(255))
    disk_serial_number: Mapped[str] = mapped_column(String(100))
    netapp_cluster_id: Mapped[str] = mapped_column(String(36))
    lun_uuid: Mapped[str] = mapped_column(String(36))
    lun_name: Mapped[str] = mapped_column(String(500))
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    volume_uuid: Mapped[str] = mapped_column(String(36))
    volume_name: Mapped[str] = mapped_column(String(255))
    # Opt-out (Nutzer-Vorgabe 2026-09-30): LUN (samt Mapping) und Volume
    # koennen auf dem Storage stehen bleiben; Volume nur zusammen mit der LUN.
    delete_lun: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_volume: Mapped[bool] = mapped_column(Boolean, default=False)
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    removed_from_groups: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "CsvDeleteRunStep", back_populates="run", cascade="all, delete-orphan", order_by="CsvDeleteRunStep.created_at",
    )


class CsvDeleteRunStep(Base):
    __tablename__ = "csv_delete_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("csv_delete_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("CsvDeleteRun", back_populates="steps")
