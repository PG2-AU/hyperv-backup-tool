import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BackupScope(str, enum.Enum):
    VM = "vm"
    CSV = "csv"
    LUN = "lun"


class ConsistencyType(str, enum.Enum):
    APPLICATION_CONSISTENT = "ApplicationConsistent"
    CRASH_CONSISTENT = "CrashConsistent"


class BackupJob(Base):
    """Backup-Job-Definition (wiederverwendbare 'Policy'). VM/CSV-Zuordnung
    (scope/targets) erfolgt aktuell separat/spaeter -- beim Anlegen ueber die
    GUI wird zunaechst nur Name, Zeitplan, Konsistenz und SnapMirror-Update
    festgelegt."""

    __tablename__ = "backup_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("schedules.id"), nullable=True)
    scope: Mapped[BackupScope | None] = mapped_column(Enum(BackupScope), nullable=True)
    targets: Mapped[list[str]] = mapped_column(JSON, default=list)
    consistency: Mapped[ConsistencyType] = mapped_column(Enum(ConsistencyType), default=ConsistencyType.CRASH_CONSISTENT)
    snapmirror_update: Mapped[bool] = mapped_column(Boolean, default=False)
    snapmirror_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metrocluster_aware: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    schedule = relationship("Schedule")
