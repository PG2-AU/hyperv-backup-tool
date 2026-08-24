from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.backup_job import BackupScope, ConsistencyType
from app.schemas.schedule import ScheduleRead


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLEANING_UP = "cleaning_up"
    CLEANED_UP_AFTER_FAILURE = "cleaned_up_after_failure"


class BackupJobCreate(BaseModel):
    name: str
    schedule_id: str | None = None
    app_consistent: bool = False
    snapmirror_update: bool = False


class BackupJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    schedule_id: str | None = None
    schedule: ScheduleRead | None = None
    scope: BackupScope | None = None
    targets: list[str]
    consistency: ConsistencyType
    snapmirror_update: bool
    snapmirror_label: str | None = None
    metrocluster_aware: bool
    enabled: bool
    created_at: datetime


class BackupJobRun(BaseModel):
    id: str
    job_id: str
    job_name: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    scope: BackupScope | None = None
    targets: list[str]
    created_snapshots: list[str] = []
    created_checkpoints: list[str] = []
    error_message: str | None = None
