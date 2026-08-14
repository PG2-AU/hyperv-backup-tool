from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class BackupScope(StrEnum):
    VM = "vm"
    CSV = "csv"
    LUN = "lun"


class ConsistencyType(StrEnum):
    APPLICATION_CONSISTENT = "ApplicationConsistent"
    CRASH_CONSISTENT = "CrashConsistent"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLEANING_UP = "cleaning_up"
    CLEANED_UP_AFTER_FAILURE = "cleaned_up_after_failure"


class BackupJobDefinition(BaseModel):
    id: str
    name: str
    scope: BackupScope
    targets: list[str]
    consistency: ConsistencyType
    schedule_cron: str | None = None
    snapmirror_label: str | None = None
    metrocluster_aware: bool = False
    enabled: bool = True


class BackupJobRun(BaseModel):
    id: str
    job_id: str
    job_name: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    scope: BackupScope
    targets: list[str]
    created_snapshots: list[str] = []
    created_checkpoints: list[str] = []
    error_message: str | None = None
