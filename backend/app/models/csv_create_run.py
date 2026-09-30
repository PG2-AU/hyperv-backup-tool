"""Persistierte Laeufe "Neue CSV anlegen" (Backlog #68): Volume + LUN auf der
NetApp anlegen, auf igroups mappen, auf allen Knoten einlesen, auf einem
Knoten formatieren, als Cluster-Disk aufnehmen und zur CSV machen.
Hintergrund-Task mit Schritt-Protokoll wie CsvResizeRun, siehe
app.api.routes.csv_create.

Die created_*-Felder merken sich, was DIESER Lauf tatsaechlich angelegt hat
-- nur das wird beim (vom Nutzer bestaetigten) Zurueckrollen wieder
entfernt, nie ein bereits vorher vorhandenes Objekt."""

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


class CsvCreateRun(Base):
    __tablename__ = "csv_create_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    netapp_cluster_id: Mapped[str] = mapped_column(String(36))
    csv_name: Mapped[str] = mapped_column(String(255))
    svm_name: Mapped[str] = mapped_column(String(255))
    volume_name: Mapped[str] = mapped_column(String(255))
    lun_name: Mapped[str] = mapped_column(String(255))
    volume_size_bytes: Mapped[int] = mapped_column(Integer)
    lun_size_bytes: Mapped[int] = mapped_column(Integer)
    # Alle uebrigen Eingaben des Assistenten (Aggregat, igroups, Dateisystem,
    # Blockgroesse, Autosize, Ordner umbenennen, Protection Group).
    options: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- von diesem Lauf angelegt (fuer das Zurueckrollen) ---
    created_volume_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_lun_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lun_serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lun_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mapped_igroups: Mapped[list] = mapped_column(JSON, default=list)
    format_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disk_formatted: Mapped[bool] = mapped_column(Boolean, default=False)
    cluster_resource_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    csv_added: Mapped[bool] = mapped_column(Boolean, default=False)
    csv_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Nutzer hat nach einem Fehler "Behalten" gewaehlt -- kein Zurueckrollen
    # mehr anbieten.
    rollback_declined: Mapped[bool] = mapped_column(Boolean, default=False)

    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # running / succeeded / failed / cleaned_up (= zurueckgerollt)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "CsvCreateRunStep", back_populates="run", cascade="all, delete-orphan", order_by="CsvCreateRunStep.created_at",
    )

    @property
    def has_created_objects(self) -> bool:
        return bool(self.created_volume_uuid or self.created_lun_uuid or self.mapped_igroups or self.cluster_resource_name)


class CsvCreateRunStep(Base):
    __tablename__ = "csv_create_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("csv_create_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("CsvCreateRun", back_populates="steps")
