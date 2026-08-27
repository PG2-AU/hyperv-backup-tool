"""Persistierte Backup-Job-Laeufe. Ein Lauf (BackupRun) besteht aus einem
oder mehreren Snapshots (BackupRunSnapshot) -- pro betroffenem NetApp-Volume
genau einer, auch wenn mehrere VMs dasselbe CSV/Volume teilen. Jeder Snapshot-
Eintrag haelt die vollstaendige Zuordnung VM(s) <-> CSV(s) <-> LUN(s) <->
NetApp-Volume <-> Snapshot, wie sie zum Zeitpunkt des Laufs aufgeloest wurde."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.backup_policy import BackupScope


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLEANING_UP = "cleaning_up"
    CLEANED_UP_AFTER_FAILURE = "cleaned_up_after_failure"


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    policy_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("backup_policies.id", ondelete="SET NULL"), nullable=True)
    policy_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    consistency: Mapped[str] = mapped_column(String(50))
    scope: Mapped[BackupScope | None] = mapped_column(Enum(BackupScope), nullable=True)
    targets: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    snapshots = relationship("BackupRunSnapshot", back_populates="run", cascade="all, delete-orphan")
    vm_configs = relationship("BackupRunVmConfig", back_populates="run", cascade="all, delete-orphan")


class BackupRunSnapshot(Base):
    """Ein Snapshot-Vorgang innerhalb eines Laufs, inkl. der vollstaendigen
    Zuordnungskette zum Zeitpunkt der Ausfuehrung (VMs/CSVs/LUNs koennen sich
    spaeter aendern -- hier bleibt fest, was zum Backup-Zeitpunkt galt)."""

    __tablename__ = "backup_run_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("backup_runs.id", ondelete="CASCADE"))
    netapp_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    netapp_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    volume_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    csv_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    lun_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    vm_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    snapshot_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("BackupRun", back_populates="snapshots")


class BackupRunVmConfig(Base):
    """Momentaufnahme der Hyper-V-VM-Konfiguration (CPU/RAM/NICs/PCI-Devices/
    VHD-Liste inkl. CSV/LUN-Zuordnung) zum Zeitpunkt eines Backup-Laufs --
    kopiert aus der zuletzt discoverten HyperVVm/HyperVVhd/HyperVCsv-DB
    (kein zusaetzlicher WinRM-Aufruf waehrend des Backups, siehe
    trigger_job_run in app.api.routes.jobs). Grundlage fuer eine kuenftige
    komplette VM-Wiederherstellung und fuer die praezise VHD->LUN-Aufloesung
    beim Restore (siehe _execute_restore in app.api.routes.restore) --
    unabhaengig davon, ob die VM zwischenzeitlich auf eine andere CSV/LUN
    umgezogen ist."""

    __tablename__ = "backup_run_vm_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("backup_runs.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Aus HyperVVm.cluster_id zum Backup-Zeitpunkt uebernommen -- einzige
    # dauerhafte Quelle dafuer, sobald die VM geloescht und aus HyperVVm
    # beim naechsten Discovery-Lauf verschwunden ist (siehe VmRecreateRun).
    hyperv_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_startup_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_minimum_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_maximum_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dynamic_memory_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    network_adapters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pci_devices: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Liste von {name, path, size_bytes, csv_name, netapp_cluster_id,
    # netapp_cluster_name, svm_name, volume_name, lun_name} -- eine Zeile
    # pro VHD dieser VM zum Backup-Zeitpunkt.
    vhds: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("BackupRun", back_populates="vm_configs")
