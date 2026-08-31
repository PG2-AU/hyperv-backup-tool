"""E-Mail-Alerting-Konfiguration (SMTP-Zugangsdaten, Empfaenger, welche
Ereignisse eine Mail ausloesen). Singleton-Zeile in der DB, analog zu
SchedulerStatus -- GUI-verwaltet statt nur per ENV (siehe Settings > E-Mail),
damit ein Admin die Konfiguration ohne Container-Neustart aendern kann."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class EmailConfig(Base):
    __tablename__ = "email_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    # "none" (Klartext) | "starttls" | "ssl" (implizites TLS, z.B. Port 465)
    smtp_encryption: Mapped[str] = mapped_column(String(20), default="starttls")
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    from_address: Mapped[str] = mapped_column(String(255), default="")
    from_name: Mapped[str] = mapped_column(String(255), default="Hyper-V NetApp Backup")
    # Komma-getrennte Liste -- eine globale Empfaengerliste reicht laut
    # Nutzer-Vorgabe, keine pro-Policy-Empfaenger noetig.
    recipients: Mapped[str] = mapped_column(String(2000), default="")

    # Backup-Fehlschlaege werden NICHT hier, sondern pro Policy geschaltet
    # (BackupPolicy.email_alert_on_failure, siehe Backup > Policies) --
    # nicht jede Policy ist gleich kritisch.
    notify_on_restore_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Lokale Stunde (0-23) in HVNB_SCHEDULE_TIMEZONE, zu der die
    # Tages-Zusammenfassung verschickt wird (siehe app.core.scheduler).
    daily_summary_hour: Mapped[int] = mapped_column(Integer, default=7)

    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.recipients.split(",") if r.strip()]
