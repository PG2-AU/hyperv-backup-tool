"""Einstellungen + Status der automatischen Sicherung der App-Datenbank
(Backlog #66, Settings > DB-Sicherung). Singleton-Zeile wie EmailConfig.
Ablauf und Wiederherstellung siehe app.core.db_backup."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class DbBackupConfig(Base):
    __tablename__ = "db_backup_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # UNC-Pfad inkl. optionalem Unterordner, z.B. \\fs01\backup\hvnb
    share_path: Mapped[str] = mapped_column(String(1000), default="")
    username: Mapped[str] = mapped_column(String(255), default="")
    encrypted_password: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Stunde (0-23, UTC wie die uebrigen taeglichen Hintergrundjobs, siehe
    # SchedulerConfig.snapshot_reconcile_hour), Minute fest :30.
    hour_utc: Mapped[int] = mapped_column(Integer, default=1)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    # Zusaetzliche lokale Kopien im /data-Volume (Nutzer-Vorgabe: 3).
    local_keep: Mapped[int] = mapped_column(Integer, default=3)

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Fehler des LETZTEN Versuchs (None = letzter Versuch erfolgreich).
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Lokale Kopie gelang, nur das Hochladen auf die Freigabe nicht.
    last_upload_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Seit wann aktiviert -- Grundlage fuer "ueberfaellig", solange es noch
    # nie eine erfolgreiche Sicherung gab.
    enabled_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
