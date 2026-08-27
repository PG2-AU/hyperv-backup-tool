"""Persistierte VM-Restore-Laeufe (Snapshot -> LUN-Klon -> iSCSI-Mount ->
SMB-Kopie -> VHDX anhaengen/ersetzen). Laeuft als Hintergrund-Task (siehe
app.api.routes.restore); Status/Schritte werden fortlaufend aktualisiert,
damit das Frontend per Polling live mitverfolgen kann (analog zur
'Laufende Backup-Jobs'-Anzeige, aber mit sichtbaren Einzelschritten statt
nur Gesamtstatus)."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RestoreMode(str, enum.Enum):
    REPLACE = "replace"
    ADD = "add"


class RestoreStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLEANED_UP = "cleaned_up"


class RestoreStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


class RestoreRun(Base):
    __tablename__ = "restore_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    source_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_vhd_path: Mapped[str] = mapped_column(String(1000))
    mode: Mapped[RestoreMode] = mapped_column(String(20))
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    restored_vhd_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    attached_controller_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attached_controller_number: Mapped[str | None] = mapped_column(String(10), nullable=True)
    attached_controller_location: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cleanup_needed: Mapped[bool] = mapped_column(default=False)
    cleanup_done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship("RestoreRunStep", back_populates="run", cascade="all, delete-orphan", order_by="RestoreRunStep.created_at")


class RestoreRunStep(Base):
    __tablename__ = "restore_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("restore_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Bytes-kopiert/Gesamtbytes fuer den Copy-Schritt, damit das Frontend
    # waehrend des laufenden Kopiervorgangs einen echten Fortschrittsbalken
    # zeigen kann (siehe HyperVService.copy_file_to_share_with_progress).
    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("RestoreRun", back_populates="steps")
