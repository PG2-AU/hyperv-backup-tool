"""Persistierte Laeufe zur kompletten Neuerstellung einer geloeschten VM aus
einem Backup-Lauf (BackupRunVmConfig liefert CPU/RAM/Generation/NICs/VHD-
Liste). Getrennt von RestoreRun/RestoreRunStep (siehe restore_run.py), da
RestoreRun strukturell auf genau eine VHDX an eine bereits bestehende VM
zugeschnitten ist -- eine Neuerstellung braucht mehrere VHDs plus
Hardware-Konfiguration. Laeuft als Hintergrund-Task (siehe
app.api.routes.restore._execute_vm_recreate); Status/Schritte werden
fortlaufend aktualisiert, damit das Frontend per Polling live mitverfolgen
kann (gleiches Muster wie RestoreRun)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.restore_run import RestoreStatus, RestoreStepStatus


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VmRecreateRun(Base):
    __tablename__ = "vm_recreate_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    hyperv_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_name: Mapped[str] = mapped_column(String(255))
    # Name, unter dem die VM in Hyper-V tatsaechlich angelegt wird. Normal-
    # fall (Neuerstellung einer geloeschten VM) == vm_name; bei einem
    # Side-by-side-Restore (Original existiert weiterhin) ein vom Nutzer
    # gewaehlter, abweichender Name. Bei alten Laeufen (vor diesem Feature)
    # ist die Spalte leer -- ueberall im Code wird dann auf vm_name
    # zurueckgefallen (siehe target_display_name-Property).
    target_vm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Side-by-side-Restore-Optionen (siehe RecreateVmRequest in restore.py):
    # bei einem Klon neben der weiterlaufenden Original-VM sinnvoll, um
    # IP-/Netzwerk-Konflikte zu vermeiden und die Kopie nicht auf dieselbe
    # CSV wie das Original zu legen. Bei der normalen Neuerstellung einer
    # geloeschten VM bleiben beide auf Default (False/None).
    disconnect_network: Mapped[bool] = mapped_column(Boolean, default=False)
    destination_csv_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_run_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[RestoreStatus] = mapped_column(String(20), default=RestoreStatus.RUNNING)
    new_vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def target_display_name(self) -> str:
        return self.target_vm_name or self.vm_name

    steps = relationship(
        "VmRecreateRunStep", back_populates="run", cascade="all, delete-orphan", order_by="VmRecreateRunStep.created_at",
    )


class VmRecreateRunStep(Base):
    __tablename__ = "vm_recreate_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("vm_recreate_runs.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[RestoreStepStatus] = mapped_column(String(20), default=RestoreStepStatus.PENDING)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    run = relationship("VmRecreateRun", back_populates="steps")
