from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.backup_policy import BackupScope, ConsistencyType, RetentionType
from app.models.backup_run import JobStatus
from app.schemas.schedule import ScheduleRead
from app.schemas.snapmirror_label import SnapMirrorLabelRead


class BackupPolicyWrite(BaseModel):
    """Gemeinsames Payload-Schema fuer Anlegen und Bearbeiten einer Backup-Policy."""

    name: str
    schedule_id: str | None = None
    app_consistent: bool = False
    snapmirror_update: bool = False
    snapmirror_label_id: str | None = None
    retention_type: RetentionType
    retention_value: int
    snapshot_locking_enabled: bool = False
    snapshot_locking_days: int | None = None

    @model_validator(mode="after")
    def _validate(self) -> "BackupPolicyWrite":
        if self.retention_value <= 0:
            raise ValueError("Retention-Anzahl muss groesser als 0 sein")

        if self.snapshot_locking_enabled:
            if not self.snapshot_locking_days or self.snapshot_locking_days <= 0:
                raise ValueError("Anzahl Tage fuer Snapshot Locking erforderlich, wenn aktiviert")
        elif self.snapshot_locking_days is not None:
            raise ValueError("Anzahl Tage fuer Snapshot Locking ist nur zulaessig, wenn Snapshot Locking aktiviert ist")

        return self


class BackupPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    schedule_id: str | None = None
    schedule: ScheduleRead | None = None
    consistency: ConsistencyType
    snapmirror_update: bool
    snapmirror_label_id: str | None = None
    snapmirror_label: SnapMirrorLabelRead | None = None
    retention_type: RetentionType
    retention_value: int
    snapshot_locking_enabled: bool
    snapshot_locking_days: int | None = None
    metrocluster_aware: bool
    enabled: bool
    created_at: datetime


class BackupRunSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    netapp_cluster_name: str | None = None
    svm_name: str | None = None
    volume_name: str | None = None
    csv_names: list[str] = []
    lun_names: list[str] = []
    vm_names: list[str] = []
    snapshot_name: str | None = None
    snapshot_uuid: str | None = None
    success: bool
    error_message: str | None = None


class BackupJobRun(BaseModel):
    id: str
    job_id: str | None = None
    job_name: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    scope: BackupScope | None = None
    targets: list[str]
    error_message: str | None = None
    snapshots: list[BackupRunSnapshotRead] = []
