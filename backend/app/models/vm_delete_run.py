"""Persistierte Laeufe "VM loeschen": Cluster-Rolle und VM entfernen, aus
Protection Groups austragen, optional die Disk-Dateien samt leer gewordener
Ordner loeschen. Hintergrund-Task mit Schritt-Protokoll, siehe
app.api.routes.vm_delete. Kein Zurueckrollen."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VmDeleteRun(Base):
    __tablename__ = "vm_delete_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    node_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Opt-out: ohne Haken wird die VM nur aus Hyper-V entfernt, die Disks bleiben.
    delete_files: Mapped[bool] = mapped_column(Boolean, default=True)
    # Laufende VM vorher hart ausschalten (sonst bricht der Lauf ab).
    turn_off: Mapped[bool] = mapped_column(Boolean, default=False)
    # Beim Start festgehaltene Dateien/Ordner (nach Remove-VM nicht mehr abfragbar).
    files: Mapped[list] = mapped_column(JSON, default=list)
    folders: Mapped[list] = mapped_column(JSON, default=list)
    removed_from_groups: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "VmDeleteRunStep", back_populates="run", cascade="all, delete-orphan", order_by="VmDeleteRunStep.created_at",
    )


class VmDeleteRunStep(Base):
    __tablename__ = "vm_delete_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("vm_delete_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("VmDeleteRun", back_populates="steps")
