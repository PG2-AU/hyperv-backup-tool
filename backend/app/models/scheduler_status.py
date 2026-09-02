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
    last_retention_cleanup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_file_restore_expiry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Ende des zuletzt ausgewerteten Zeitfensters von run_scheduled_backups
    # (app.core.scheduler) -- NICHT nur ein Anzeige-Zeitstempel wie die
    # anderen Felder hier, sondern die Grundlage fuer den Nachhol-Mechanismus:
    # ein Tick prueft (letzter Check, jetzt] statt nur die exakt aktuelle
    # Minute, damit ein durch einen laenger laufenden vorherigen Lauf
    # uebersprungener Tick keinen faelligen Zeitplan endgueltig verschluckt.
    last_scheduled_backup_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ISO-Datum ("YYYY-MM-DD", lokale Zeitzone HVNB_SCHEDULE_TIMEZONE) des
    # zuletzt verschickten Tages-E-Mail-Summarys -- verhindert Doppelversand,
    # da run_daily_email_summary alle 15 Minuten prueft, ob die konfigurierte
    # Stunde erreicht ist (siehe app.core.scheduler).
    last_email_summary_sent_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
