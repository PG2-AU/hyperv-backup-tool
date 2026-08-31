"""Persistierte System-/Hintergrund-Meldungen (Scheduler-Jobs: Health-Check,
Discovery, Snapshot-Abgleich, Retention-Cleanup, geplante Backups,
Datei-Restore-Sicherheitsnetz). Vorher wurden diese Meldungen ausschliesslich
per print() ins Container-Log geschrieben (siehe historische _log()-Funktion
in app.core.scheduler) und waren damit fuer die GUI ("System Log", siehe
app.api.routes.logs) nicht abruf-/durchsuchbar -- nur ueber `podman logs`
einsehbar."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SystemLogEvent(Base):
    __tablename__ = "system_log_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    source: Mapped[str] = mapped_column(String(50), default="scheduler")
    message: Mapped[str] = mapped_column(String(2000))
