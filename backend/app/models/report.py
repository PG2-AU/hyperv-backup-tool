"""Reports (Backlog #84, Nutzer-Vorgaben 2026-10-03): eigener Menuepunkt mit
"Neuer Report", "Gespeicherte Reports" und "Historie". Siehe app.core.reports
fuer Inhalte/PDF und app.api.routes.reports fuer die API.

ReportDefinition = gespeicherte Vorlage (Typ + Auswahl), optional mit
Zeitplan und Mail-Empfaengern. ReportRun = ein erzeugter Report (PDF, optional
CSV) in der Historie, 12 Monate aufbewahrt."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReportDefinition(Base):
    __tablename__ = "report_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(255))
    report_type: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # none | daily | weekly | monthly
    schedule_type: Mapped[str] = mapped_column(String(20), default="none")
    # weekly: 0=Montag .. 6=Sonntag; monthly: Tag 1..28
    schedule_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_time: Mapped[str] = mapped_column(String(5), default="06:00")
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    # Nur senden, wenn der Report Auffaelligkeiten enthaelt.
    only_if_findings: Mapped[bool] = mapped_column(Boolean, default=False)
    attach_csv: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Zuletzt abgearbeiteter Zeitplan-Termin (nicht der Ausfuehrungszeitpunkt):
    # Grundlage dafuer, dass jeder Termin genau einmal laeuft.
    last_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    definition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    definition_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    report_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    # Zeitraum bzw. Auswahl in Kurzform, wie im PDF-Kopf.
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # Benutzer bzw. "Zeitplan: <Name>"
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # succeeded | failed
    status: Mapped[str] = mapped_column(String(20), default="succeeded")
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    findings: Mapped[int] = mapped_column(Integer, default=0)
    findings_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    csv_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # SHA-256 der PDF-Datei (Integritaet der Datei) und des Inhalts (steht auch
    # im Fuss des PDF -- eine Datei kann ihre eigene Pruefsumme nicht tragen).
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emailed_to: Mapped[list] = mapped_column(JSON, default=list)
    email_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
