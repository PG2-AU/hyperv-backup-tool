"""Aenderungsprotokoll (Backlog #84, Report "Audit-Trail"): jede aendernde
API-Anfrage eines angemeldeten Benutzers (POST/PUT/PATCH/DELETE) mit Wer,
Wann, Was, Ziel und Ergebnis. Geschrieben von app.core.audit.AuditMiddleware
-- zentral statt in jedem einzelnen Endpunkt, damit keine Aenderung fehlt
(bis dahin protokollierten nur einzelne Endpunkte, z.B. CSV anlegen, ins
System-Log; Policies, Protection Groups, Zeitplaene, Systeme oder
Einstellungen gar nicht). Aufbewahrung wie die Reports (13 Monate, siehe
app.core.reports.purge_old_reports)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_timestamp", "timestamp"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    username: Mapped[str] = mapped_column(String(255))
    # Bereich ("Policy", "Protection Group", ...) und Aktion ("angelegt", ...)
    area: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    # Betroffenes Objekt (Name), soweit ermittelbar
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(1000))
    status_code: Mapped[int] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(100), nullable=True)
