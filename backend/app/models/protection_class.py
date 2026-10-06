"""Schutzklassen (Backlog #86, Nutzer-Vorgabe 2026-10-06): frei definierbare
Klassen (z.B. Gold/Silber/Bronze) als Soll-Vorgabe fuer die Sicherung --
maximales Backup-Alter, Aufbewahrung primaer und sekundaer (SnapMirror-Ziel)
in Tagen und Applikationskonsistenz. Jede VM und jede CSV/SMB3-Freigabe bekommt ihre
Klasse von Hand zugewiesen; geprueft wird, ob das Objekt seiner Klasse
entsprechend gesichert wird und ob eine VM auf Speicher mindestens ihrer
Klasse liegt (rank: 1 = hoechste). Pruefung in app.core.protection_class."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProtectionClass(Base):
    __tablename__ = "protection_classes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Rangfolge, 1 = hoechste Klasse. Speicher muss mindestens die Klasse der VM haben.
    rank: Mapped[int] = mapped_column(Integer, default=1)
    color: Mapped[str] = mapped_column(String(20), default="blue")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    max_backup_age_hours: Mapped[int] = mapped_column(Integer, default=26)
    # Aufbewahrung PRIMAER in Tagen (Spaltenname aus der ersten Fassung).
    min_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    # Aufbewahrung SEKUNDAER in Tagen, 0/None = keine sekundaere Kopie verlangt
    # (Nutzer-Vorgabe 2026-10-06, ersetzt den Ja/Nein-Schalter require_secondary).
    secondary_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # Altlast (durch secondary_retention_days ersetzt, in init_db ueberfuehrt);
    # bleibt, weil SQLite NOT-NULL-Spalten nicht entfernen kann.
    require_secondary: Mapped[bool] = mapped_column(Boolean, default=False)
    require_app_consistent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProtectionClassAssignment(Base):
    """Zuordnung Objekt -> Klasse. Schluessel wie bei Protection Groups:
    Hyper-V-Cluster + Name (VM-Name, CSV-Name bzw. 'server|share'). Bei VMs
    zusaetzlich die Hyper-V-VM-ID, damit die Zuordnung eine Umbenennung
    uebersteht."""

    __tablename__ = "protection_class_assignments"
    __table_args__ = (Index("ix_pc_assignment_object", "object_type", "cluster_id", "object_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    object_type: Mapped[str] = mapped_column(String(20))  # vm | csv | smb_share
    cluster_id: Mapped[str] = mapped_column(String(36))
    object_name: Mapped[str] = mapped_column(String(500))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    class_id: Mapped[str] = mapped_column(String(36), ForeignKey("protection_classes.id", ondelete="CASCADE"))
    assigned_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
