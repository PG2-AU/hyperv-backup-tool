"""Standorte (Rechenzentren) fuer die Standort-Kennzeichnung von Hyper-V-
Knoten und Storage (Nutzer-Vorgabe 2026-09-25): eine VM, die auf einem Host
in DC1 laeuft, soll auch auf einer CSV in DC1 liegen. Rein kennzeichnend --
die App migriert nichts selbst, sie zeigt Abweichungen nur an (Inventory-
Badge, Dashboard, Alarm HYPERV_VM_SITE_MISMATCH).

Zuordnung:
- Hyper-V-Knoten: manuell pro Knoten (HyperVNodeSite), bewusst ohne
  automatisches Auslesen der Windows-Cluster-Fault-Domains (Nutzer-
  Entscheidung).
- Storage: pro registriertem NetApp-System (NetAppCluster.site_id) -- bei
  MetroCluster ist jede Site ein eigener ONTAP-Cluster, jede CSV erbt den
  Standort ueber ihre LUN-Seriennummer. Einzelne CSVs koennen per
  CsvSiteOverride abweichend markiert werden.
Auswertung siehe app.core.sites."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Mantine-Farbname fuer die Badges (z.B. "blue", "grape") -- rein optisch.
    color: Mapped[str] = mapped_column(String(30), default="blue")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVNodeSite(Base):
    """Manuelle Zuordnung Hyper-V-Knoten -> Standort. node_name wird immer
    normalisiert gespeichert (Kleinbuchstaben, ohne DNS-Suffix, siehe
    app.core.sites.normalize_node_name) -- HyperVVm.host_name und
    Get-ClusterNode liefern nicht zuverlaessig dieselbe Schreibweise (vgl.
    die Gross-/Kleinschreibungs-Falle bei CSV-Ordnernamen, 7494cd1)."""

    __tablename__ = "hyperv_node_sites"
    __table_args__ = (UniqueConstraint("cluster_id", "node_name", name="uq_hyperv_node_site"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    node_name: Mapped[str] = mapped_column(String(255))
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("sites.id", ondelete="CASCADE"))


class CsvSiteOverride(Base):
    """Abweichender Standort fuer eine einzelne CSV, unabhaengig vom
    Standort ihres NetApp-Systems. Schluessel ist die Windows-Disk-
    Seriennummer (= ONTAP lun.serial_number), NICHT der CSV-Name oder die
    HyperVCsv.id: beide sind nicht stabil (Name umbenennbar, die Zeile wird
    bei jeder Discovery geloescht und neu angelegt)."""

    __tablename__ = "csv_site_overrides"
    __table_args__ = (UniqueConstraint("cluster_id", "disk_serial_number", name="uq_csv_site_override"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    disk_serial_number: Mapped[str] = mapped_column(String(100))
    # Nur zur Anzeige, falls die CSV gerade nicht discovert ist.
    csv_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("sites.id", ondelete="CASCADE"))


class VmSiteMismatchObservation(Base):
    """Seit wann run_alert_check eine Standort-Abweichung einer VM
    ununterbrochen beobachtet -- Grundlage fuer die Karenzzeit
    (AlertConfig.site_mismatch_grace_minutes). Eigene Tabelle statt einer
    Spalte an HyperVVm, da die VM-Zeilen bei jeder Discovery geloescht und
    neu angelegt werden. Zeile verschwindet, sobald die Abweichung weg ist."""

    __tablename__ = "vm_site_mismatch_observations"

    vm_uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
