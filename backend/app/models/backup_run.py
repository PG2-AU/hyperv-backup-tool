"""Persistierte Backup-Job-Laeufe. Ein Lauf (BackupRun) besteht aus einem
oder mehreren Snapshots (BackupRunSnapshot) -- pro betroffenem NetApp-Volume
genau einer, auch wenn mehrere VMs dasselbe CSV/Volume teilen. Jeder Snapshot-
Eintrag haelt die vollstaendige Zuordnung VM(s) <-> CSV(s) <-> LUN(s) <->
NetApp-Volume <-> Snapshot, wie sie zum Zeitpunkt des Laufs aufgeloest wurde."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.backup_policy import BackupScope
from app.models.restore_run import RestoreStepStatus


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
    # Nur bei einem geplanten Lauf gesetzt (siehe run_scheduled_backups in
    # scheduler.py): welche EINE Resource Group faellig war und diesen Lauf
    # ausgeloest hat -- ein manuelles "Jetzt ausfuehren" auf der ganzen
    # Policy (alle verknuepften Resource Groups zusammen) laesst das Feld
    # leer. Ermoeglicht, den "laeuft bereits"-Schutz pro Resource Group statt
    # pro ganzer Policy zu pruefen (siehe _start_job_run) -- zwei
    # verschiedene, zeitversetzt geplante Resource Groups derselben Policy
    # sollen sich nicht gegenseitig blockieren.
    resource_group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resource_groups.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    consistency: Mapped[str] = mapped_column(String(50))
    scope: Mapped[BackupScope | None] = mapped_column(Enum(BackupScope), nullable=True)
    targets: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Der "Backup fehlgeschlagen"-Alarm zu diesem Lauf (siehe
    # app.api.routes.alerts.list_alerts) ist rein virtuell aus dem
    # BackupRun-Verlauf abgeleitet -- loest sich von selbst nur auf, wenn ein
    # SPAETERER Lauf derselben Policy erfolgreich war. Bei einer selten
    # laufenden Policy oder einer inzwischen deaktivierten/geloeschten
    # Policy kann das nie eintreten -- der Alarm bliebe sonst fuer immer
    # aktiv (Nutzer-Meldung). Manuelles Quittieren setzt dieses Feld.
    alert_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    snapshots = relationship("BackupRunSnapshot", back_populates="run", cascade="all, delete-orphan")
    vm_configs = relationship("BackupRunVmConfig", back_populates="run", cascade="all, delete-orphan")
    steps = relationship("BackupRunStep", back_populates="run", cascade="all, delete-orphan", order_by="BackupRunStep.created_at")
    # Nur befuellt, wenn resource_group_id gesetzt ist (siehe oben) --
    # fuer die Anzeige des Protection-Group-Namens in den Zeitstrahl-
    # Ansichten (Dashboard/Backup > Kalender).
    resource_group = relationship("ResourceGroup")


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
    destinations = relationship("BackupRunSnapshotDestination", back_populates="snapshot", cascade="all, delete-orphan")


class BackupRunSnapshotDestination(Base):
    """Ob ein Snapshot per SnapMirror auf ein Ziel-Volume repliziert wurde
    -- eine Zeile pro (Snapshot, Ziel), da eine Quelle mehrere Ziele haben
    kann (Fan-out, z.B. eine Quelle wird sowohl ins Trainings- als auch ins
    Demo-Environment gespiegelt). Wird periodisch von
    app.core.scheduler.run_snapshot_reconciliation aktualisiert: pro
    erfolgreichem BackupRunSnapshot werden die dafuer discoverten
    NetAppSnapMirrorRelationship-Zeilen aufgeloest und am jeweiligen
    Ziel-Volume geprueft, ob der (namensgleiche, SnapMirror aendert den
    Namen beim Transfer nicht) Snapshot dort tatsaechlich angekommen ist --
    haengt vom SnapMirror-Label der Policy ab, ob das ueberhaupt der Fall
    ist (siehe Retention-Regeln der auf der Beziehung aktiven SnapMirror-
    Policy, SnapMirrorCheckPanel.tsx zeigt diese an).

    Grundlage fuer den Restore-von-SnapMirror-Destination-Workflow (siehe
    app.api.routes.restore) -- ein bestaetigt vorhandener Ziel-Snapshot
    kann als alternative Quelle fuer den LUN-Klon verwendet werden, z.B.
    wenn die urspruengliche Quelle nicht erreichbar ist oder deren eigene
    Snapshot-Retention den Snapshot bereits entfernt hat, das Ziel ihn aber
    noch haelt (eigener, unabhaengiger Lebenszyklus -- live verifiziert:
    ein auf der Quelle geloeschter Snapshot blieb auf dem Ziel bestehen)."""

    __tablename__ = "backup_run_snapshot_destinations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    backup_run_snapshot_id: Mapped[str] = mapped_column(String(36), ForeignKey("backup_run_snapshots.id", ondelete="CASCADE"))
    relationship_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Unser eigener registrierter NetAppCluster, sofern der Ziel-Cluster
    # (per destination_cluster_name aus der discoverten Beziehung) mit
    # einem in dieser App bekannten Cluster uebereinstimmt -- ohne das kann
    # die Praesenz zwar (bei geteiltem Cluster wie im Demo-Setup) trotzdem
    # geprueft werden, ein Restore davon ist aber nur mit registriertem
    # Cluster + passender RestoreInfraConfig fuer die Ziel-SVM moeglich.
    destination_netapp_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    destination_netapp_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destination_svm_name: Mapped[str] = mapped_column(String(255))
    destination_volume_name: Mapped[str] = mapped_column(String(255))
    destination_volume_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    snapshot = relationship("BackupRunSnapshot", back_populates="destinations")


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


class BackupRunStep(Base):
    """Detaillierter Schritt-fuer-Schritt-Verlauf eines Backup-Laufs
    (Checkpoint erstellen/entfernen, Snapshot je Volume, SnapMirror-Update)
    -- analog zu RestoreRunStep/VmRecreateRunStep, siehe deren _StepCtx in
    app.api.routes.restore, hier wiederverwendet fuer trigger_job_run.
    Grundlage fuer den echten Job-Log-Viewer (GET /api/logs), der bisher
    nur Demo-Daten zeigte."""

    __tablename__ = "backup_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("backup_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("BackupRun", back_populates="steps")
