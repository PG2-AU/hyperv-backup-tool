"""Historische Kapazitaets-Messpunkte fuer VHDs, CSVs, LUNs, Volumes und
Aggregate (Settings-freies Feature, siehe app.core.capacity_history fuer
die Schluesselableitung und app.core.scheduler.run_capacity_history_sampling
fuer den taeglichen Sammel-Job).

Eine gemeinsame Tabelle fuer alle fuenf Objekttypen statt fuenf einzelner --
object_key ist ein aus stabilen Objekteigenschaften abgeleiteter Schluessel
(NICHT die ephemere id-Spalte der jeweiligen Discovery-Tabelle, die bei
jedem Discovery-Zyklus als neue Zufalls-UUID neu vergeben wird)."""

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


class CapacitySample(Base):
    __tablename__ = "capacity_samples"
    __table_args__ = (Index("ix_capacity_samples_lookup", "object_type", "object_key", "sampled_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    object_type: Mapped[str] = mapped_column(String(20))  # vhd | csv | lun | volume | aggregate
    object_key: Mapped[str] = mapped_column(String(1100))
    # Anzeigename zum Messzeitpunkt -- falls das Objekt spaeter umbenannt
    # oder geloescht wird, bleibt die Historie trotzdem lesbar beschriftet.
    object_name: Mapped[str] = mapped_column(String(500))
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
