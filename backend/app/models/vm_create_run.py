"""Persistierte Laeufe "Neue VM anlegen" (Backlog #74), Hintergrund-Task mit
Schritt-Protokoll wie CsvCreateRun, siehe app.api.routes.vm_create. Die
created_*-Felder merken sich, was DIESER Lauf angelegt hat -- nur das
entfernt das vom Nutzer bestaetigte Zurueckrollen."""

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


class VmCreateRun(Base):
    __tablename__ = "vm_create_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    node_name: Mapped[str] = mapped_column(String(255))
    # Wurzel der Ablage (C:\ClusterStorage\<CSV> bzw. \\server\share) und der
    # von New-VM darunter angelegte Ordner <Wurzel>\<VM-Name>.
    storage_root: Mapped[str] = mapped_column(String(1000))
    vm_folder: Mapped[str] = mapped_column(String(1000))
    # Alle Eingaben des Assistenten (Hardware, Disks, Netzwerk, Firmware, ISO ...).
    options: Mapped[dict] = mapped_column(JSON, default=dict)

    new_vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vm_created: Mapped[bool] = mapped_column(Boolean, default=False)
    cluster_role_added: Mapped[bool] = mapped_column(Boolean, default=False)
    rollback_declined: Mapped[bool] = mapped_column(Boolean, default=False)

    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "VmCreateRunStep", back_populates="run", cascade="all, delete-orphan", order_by="VmCreateRunStep.created_at",
    )

    @property
    def has_created_objects(self) -> bool:
        return bool(self.vm_created or self.cluster_role_added)


class VmCreateRunStep(Base):
    __tablename__ = "vm_create_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("vm_create_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("VmCreateRun", back_populates="steps")
