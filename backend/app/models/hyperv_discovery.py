"""Persistierte Ergebnisse der Hyper-V-Cluster-Discovery (VMs inkl. ihrer
VHDs). Pro Cluster wird bei jedem Discovery-Lauf, der mindestens einen
erreichbaren Knoten hatte, die komplette VM-/VHD-Liste ersetzt (Replace-
Strategie), analog zur NetApp-Discovery in netapp_discovery.py."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HyperVVm(Base):
    __tablename__ = "hyperv_vms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    host_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # VM-Konfiguration (siehe HyperVService.list_vms) -- fuer die VM-Details
    # im Inventory sowie als Quelle fuer die pro Backup-Lauf kopierte
    # BackupRunVmConfig (siehe app.models.backup_run).
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_startup_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_minimum_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_maximum_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dynamic_memory_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    network_adapters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pci_devices: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVVhd(Base):
    __tablename__ = "hyperv_vhds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    path: Mapped[str] = mapped_column(String(1000))
    csv_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVCsv(Base):
    """Cluster Shared Volumes je Hyper-V-Cluster. Die Zuordnung zum
    zugrunde liegenden NetApp-LUN/-Volume erfolgt beim Discovery-Lauf ueber
    den Vergleich der Windows-Disk-Seriennummer (Get-Disk) mit dem
    lun.serial_number-Feld der bereits registrierten NetApp-LUNs -- gegen
    echte Hardware verifiziert, dass beide Werte identisch sind. Wird keine
    passende LUN gefunden (z.B. NetApp-Cluster noch nicht registriert/
    discovered), bleiben die netapp_*-Felder leer."""

    __tablename__ = "hyperv_csvs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    owner_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    netapp_lun_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    netapp_lun_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    netapp_volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
