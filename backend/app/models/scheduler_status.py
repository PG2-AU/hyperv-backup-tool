"""Letzte Ausfuehrungszeitpunkte der periodischen Hintergrund-Jobs (siehe
app.core.scheduler) -- als Singleton-Zeile in der DB statt im Prozess-
Speicher, damit die Fusszeile im Hauptmenue nach einem Redeploy (der
Container-Prozess wird dabei neu gestartet) nicht faelschlich 'nie
gelaufen' anzeigt."""

import uuid
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class SchedulerStatus(Base):
    __tablename__ = "scheduler_status"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_discovery_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_snapshot_reconciliation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
