import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BackupScope(str, enum.Enum):
    VM = "vm"
    CSV = "csv"
    LUN = "lun"


class ConsistencyType(str, enum.Enum):
    APPLICATION_CONSISTENT = "ApplicationConsistent"
    CRASH_CONSISTENT = "CrashConsistent"


class RetentionType(str, enum.Enum):
    DAYS = "days"
    COUNT = "count"


class BackupPolicy(Base):
    """Backup-Policy (frueher 'Job-Definition'): wiederverwendbare Regel aus
    Zeitplan, Konsistenz-Modus, SnapMirror-Verhalten, Retention und optionalem
    Snapshot Locking. VM/CSV-Zuordnung (scope/targets) erfolgt separat/spaeter."""

    __tablename__ = "backup_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("schedules.id"), nullable=True)
    scope: Mapped[BackupScope | None] = mapped_column(Enum(BackupScope), nullable=True)
    targets: Mapped[list[str]] = mapped_column(JSON, default=list)
    consistency: Mapped[ConsistencyType] = mapped_column(Enum(ConsistencyType), default=ConsistencyType.CRASH_CONSISTENT)
    snapmirror_update: Mapped[bool] = mapped_column(Boolean, default=False)
    snapmirror_label_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("snapmirror_labels.id"), nullable=True)
    retention_type: Mapped[RetentionType] = mapped_column(Enum(RetentionType), default=RetentionType.COUNT)
    retention_value: Mapped[int] = mapped_column(Integer, default=7)
    snapshot_locking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot_locking_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrocluster_aware: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    schedule = relationship("Schedule")
    snapmirror_label = relationship("SnapMirrorLabel")
