"""Persistierte Datei-Restore-Sessions: eine VHDX wird aus einem
Backup-Snapshot geklont, per iSCSI auf dem Restore-Proxy-Host eingebunden
und dort per Mount-VHD direkt gemountet, damit ihr Dateisystem in der GUI
durchsucht werden kann (siehe app.api.routes.file_restore). Getrennt von
RestoreRun/RestoreRunStep (siehe restore_run.py), da hier kein VM-Attach
stattfindet, sondern ein laenger offener 'gemountet und durchsuchbar'-
Zustand -- der Nutzer kann mehrfach durchsuchen/kopieren, bevor er (manuell
oder per automatischem Zeitlimit, siehe app.core.scheduler) aufraeumt.

Status-Verlauf (RestoreStatus aus restore_run.py wiederverwendet):
RUNNING (wird gemountet) -> SUCCEEDED (offen/durchsuchbar, cleanup_needed=
True) -> CLEANED_UP (VHD dismounted, LUN-Klon/iSCSI-Session entfernt)."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FileRestoreRun(Base):
    __tablename__ = "file_restore_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    vm_name: Mapped[str] = mapped_column(String(255))
    source_snapshot_id: Mapped[str] = mapped_column(String(36))
    source_vhd_path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)

    # Proxy-Pfad, unter dem die VHDX-Partition gemountet ist -- Wurzel fuer
    # Browse/Copy, jeder angefragte Pfad wird dagegen validiert (siehe
    # file_restore.py, Schutz gegen Pfad-Traversal auf den Proxy).
    browse_root_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Bei Erstellung berechneter Vorschlag fuer den Kopier-Zielpfad, im
    # Frontend vorausgefuellt und editierbar.
    default_destination_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Zwischenzustaende, die fuer den (zeitlich spaeteren, separaten)
    # Cleanup-Request benoetigt werden -- analog zu den lokalen Variablen in
    # _execute_restore, hier aber persistiert statt nur im Prozessspeicher
    # des Hintergrund-Tasks zu leben.
    disk_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vhd_disk_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_lun_mount_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Pfad der VHDX-Datei selbst, innerhalb von proxy_lun_mount_dir gefunden
    # -- wird beim Cleanup fuer Dismount-VHD -Path benoetigt.
    vhd_file_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    proxy_vhd_mount_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    clone_lun_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    clone_lun_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    igroup_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_iqn: Mapped[str | None] = mapped_column(String(255), nullable=True)

    cleanup_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps = relationship(
        "FileRestoreRunStep", back_populates="run", cascade="all, delete-orphan", order_by="FileRestoreRunStep.created_at",
    )

    @property
    def expires_at(self) -> datetime | None:
        """Zeitpunkt, zu dem das automatische Sicherheitsnetz
        (app.core.scheduler.run_file_restore_expiry) diese Session
        aufraeumt, falls der Nutzer es nicht vorher manuell tut -- fuer die
        Anzeige in der Restore-Uebersicht ('Offene Datei-Restore-
        Sessions'). Import hier statt am Modulkopf, um einen Zirkelbezug
        beim App-Start zu vermeiden (app.core.config importiert nichts aus
        app.models, aber viele Module importieren frueh von app.models)."""
        if not self.cleanup_needed:
            return None
        from app.core.config import get_settings

        return self.started_at + timedelta(hours=get_settings().file_restore_max_age_hours)


class FileRestoreRunStep(Base):
    __tablename__ = "file_restore_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("file_restore_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("FileRestoreRun", back_populates="steps")
