"""Persistierte Laeufe zur kompletten Neuerstellung einer geloeschten VM aus
einem Backup-Lauf (BackupRunVmConfig liefert CPU/RAM/Generation/NICs/VHD-
Liste). Getrennt von RestoreRun/RestoreRunStep (siehe restore_run.py), da
RestoreRun strukturell auf genau eine VHDX an eine bereits bestehende VM
zugeschnitten ist -- eine Neuerstellung braucht mehrere VHDs plus
Hardware-Konfiguration. Laeuft als Hintergrund-Task (siehe
app.api.routes.restore._execute_vm_recreate); Status/Schritte werden
fortlaufend aktualisiert, damit das Frontend per Polling live mitverfolgen
kann (gleiches Muster wie RestoreRun)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VmRecreateRun(Base):
    __tablename__ = "vm_recreate_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    source_run_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    new_vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "VmRecreateRunStep", back_populates="run", cascade="all, delete-orphan", order_by="VmRecreateRunStep.created_at",
    )


class VmRecreateRunStep(Base):
    __tablename__ = "vm_recreate_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("vm_recreate_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("VmRecreateRun", back_populates="steps")
