"""GUI-konfigurierbare Zeitplaene der periodischen Hintergrundjobs
(Health-Check, Discovery, Snapshot-Abgleich, Retention-Cleanup, siehe
app.core.scheduler) -- Singleton-Zeile in der DB, analog zu EmailConfig.
Ersetzt die bisherige reine ENV-Konfiguration (HVNB_HEALTHCHECK_INTERVAL_
MINUTES etc. in app.core.config), die weiterhin nur als Startwert fuer die
allererste Zeile dient (siehe init_db.py)."""

import uuid
from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class SchedulerConfig(Base):
    __tablename__ = "scheduler_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    healthcheck_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    discovery_interval_minutes: Mapped[int] = mapped_column(Integer, default=240)
    # 0-23 UTC (nicht HVNB_SCHEDULE_TIMEZONE wie die Backup-Zeitplaene --
    # der APScheduler-Server selbst laeuft komplett mit timezone="UTC",
    # siehe start_scheduler in app.core.scheduler).
    snapshot_reconcile_hour: Mapped[int] = mapped_column(Integer, default=2)
    retention_cleanup_hour: Mapped[int] = mapped_column(Integer, default=2)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
