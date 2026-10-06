"""Persistierte Laeufe "VM-Einstellungen aendern" (Backlog #81): CPU, RAM,
Netzwerkadapter und Festplatten einer vorhandenen VM. Hintergrund-Task mit
Schritt-Protokoll, siehe app.api.routes.vm_settings. Kein Zurueckrollen --
`changes` haelt fest, was beantragt war, die Schritte, was davon lief."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VmSettingsRun(Base):
    __tablename__ = "vm_settings_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    node_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Die beantragten Aenderungen (VmSettingsRequest als dict) und ihre
    # lesbare Zusammenfassung fuer Protokoll/Anzeige.
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    changes: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "VmSettingsRunStep", back_populates="run", cascade="all, delete-orphan", order_by="VmSettingsRunStep.created_at",
    )


class VmSettingsRunStep(Base):
    __tablename__ = "vm_settings_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("vm_settings_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("VmSettingsRun", back_populates="steps")
