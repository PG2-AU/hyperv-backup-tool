"""Misst die tatsaechliche Kopiergeschwindigkeit jedes Restore-Kopiervorgangs
(Nutzer-Vorgabe 2026-09-18), um dem Nutzer im Wizard VOR dem Start eine
ungefaehre Restore-Dauer fuer die jeweilige VHD-Groesse zeigen zu koennen.

Getrennt nach storage_type ("csv" vs. "smb3"), da beide Pfade technisch
grundverschieden kopieren (LUN-Klon+iSCSI+SMB-Kopie auf den Proxy-Host vs.
direkte UNC-zu-UNC-Kopie) und damit typischerweise unterschiedliche
Durchsatzraten haben -- ein gemeinsamer Durchschnitt waere irrefuehrend.

Erfasst bewusst nur die Basis-/Leaf-Datei je Kopiervorgang (nicht zusaetzlich
kopierte AVHDX-Vorfahren einer Checkpoint-Kette) -- die grosse Mehrheit der
Restores betrifft eine reine VHDX ohne Checkpoint, und diese Vereinfachung
haelt die Messung an einer einzigen, klar abgegrenzten Stelle je
Kopiervorgang."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RestoreCopySpeedSample(Base):
    __tablename__ = "restore_copy_speed_samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    storage_type: Mapped[str] = mapped_column(String(20))
    bytes_copied: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
