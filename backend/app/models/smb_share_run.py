"""Persistierte Laeufe "SMB3-Freigabe anlegen" und "SMB3-Freigabe loeschen"
(Gegenstuecke zu CsvCreateRun/CsvDeleteRun), siehe app.api.routes.smb_create
und app.api.routes.smb_delete. Hintergrund-Tasks mit Schritt-Protokoll.

SmbCreateRun merkt sich (wie CsvCreateRun), was der Lauf selbst angelegt hat
-- nur das entfernt das vom Nutzer bestaetigte Zurueckrollen."""

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


class SmbCreateRun(Base):
    __tablename__ = "smb_create_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    netapp_cluster_id: Mapped[str] = mapped_column(String(36))
    svm_name: Mapped[str] = mapped_column(String(255))
    volume_name: Mapped[str] = mapped_column(String(255))
    share_name: Mapped[str] = mapped_column(String(255))
    volume_size_bytes: Mapped[int] = mapped_column(Integer)
    # Aggregat, Autosize, Konten fuer die Freigaberechte, Protection Group.
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    cifs_server: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_volume_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    share_created: Mapped[bool] = mapped_column(Boolean, default=False)
    rollback_declined: Mapped[bool] = mapped_column(Boolean, default=False)

    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "SmbCreateRunStep", back_populates="run", cascade="all, delete-orphan", order_by="SmbCreateRunStep.created_at",
    )

    @property
    def has_created_objects(self) -> bool:
        return bool(self.created_volume_uuid or self.share_created)

    @property
    def unc_path(self) -> str | None:
        return f"\\\\{self.cifs_server}\\{self.share_name}" if self.cifs_server else None


class SmbCreateRunStep(Base):
    __tablename__ = "smb_create_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("smb_create_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("SmbCreateRun", back_populates="steps")


class SmbDeleteRun(Base):
    __tablename__ = "smb_delete_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    server: Mapped[str] = mapped_column(String(255))
    share: Mapped[str] = mapped_column(String(255))
    netapp_cluster_id: Mapped[str] = mapped_column(String(36))
    svm_name: Mapped[str] = mapped_column(String(255))
    volume_uuid: Mapped[str] = mapped_column(String(36))
    volume_name: Mapped[str] = mapped_column(String(255))
    # Opt-out wie bei CSV loeschen: Volume nur, wenn keine weitere Freigabe
    # und keine LUN darin liegt.
    delete_volume: Mapped[bool] = mapped_column(Boolean, default=False)
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    removed_from_groups: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "SmbDeleteRunStep", back_populates="run", cascade="all, delete-orphan", order_by="SmbDeleteRunStep.created_at",
    )


class SmbDeleteRunStep(Base):
    __tablename__ = "smb_delete_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("smb_delete_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("SmbDeleteRun", back_populates="steps")
