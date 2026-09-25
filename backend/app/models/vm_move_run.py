"""Persistierte VM-Verschiebungen ueber die App (Nutzer-Vorgabe 2026-09-25,
Stufe 1: Host-Move per Live-Migration innerhalb des Failover-Clusters).
Laeuft als Hintergrund-Task (siehe app.api.routes.vm_moves); Status/
Schritte werden fortlaufend aktualisiert, damit das Frontend per Polling
live mitverfolgen kann (gleiches Muster wie VmRecreateRun).

move_type: 'host' (Stufe 1, Live-Migration auf anderen Knoten) oder
'storage' (Stufe 2, Move-VMStorage auf eine andere CSV unter Beibehaltung
der Ordnerstruktur, siehe app.core.storage_move)."""

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


class VmMoveRun(Base):
    __tablename__ = "vm_move_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    move_type: Mapped[str] = mapped_column(String(20), default="host")
    # Owner-Knoten laut Cluster direkt vor der Migration (nicht aus der
    # ggf. veralteten Discovery), gesetzt im Vorab-Check.
    source_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_node: Mapped[str] = mapped_column(String(255))
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Nur Storage-Move (Stufe 2): Ziel-CSV, Fortschritt 0-100 laut Hyper-V,
    # Abbruch-Anforderung aus der GUI (der Lauf bricht den WMI-
    # Migrationsjob beim naechsten Fortschritts-Poll ab).
    destination_csv_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "VmMoveRunStep", back_populates="run", cascade="all, delete-orphan", order_by="VmMoveRunStep.created_at",
    )


class VmMoveRunStep(Base):
    __tablename__ = "vm_move_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("vm_move_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("VmMoveRun", back_populates="steps")
