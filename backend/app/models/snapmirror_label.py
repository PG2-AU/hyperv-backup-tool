import uuid
from datetime import datetime, timezone

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime

DEFAULT_SNAPMIRROR_LABELS = ["hyperv_hourly", "hyperv_daily", "hyperv_weekly", "hyperv_monthly"]


class SnapMirrorLabel(Base):
    """Benannte SnapMirror-Labels, mit denen Snapshots getaggt werden, damit
    SnapMirror-Retention-Regeln auf dem Zielsystem greifen. Wird von
    Backup-Policies referenziert (siehe app.models.backup_policy)."""

    __tablename__ = "snapmirror_labels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
