from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SchedulerConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    healthcheck_interval_minutes: int
    discovery_interval_minutes: int
    snapshot_reconcile_hour: int
    retention_cleanup_hour: int
    backup_cancel_force_timeout_minutes: int
    backup_run_max_duration_minutes: int
    backup_checkpoint_parallelism: int
    updated_at: datetime | None = None


class SchedulerConfigUpdate(BaseModel):
    healthcheck_interval_minutes: int = Field(ge=1, le=1440)
    discovery_interval_minutes: int = Field(ge=1, le=1440)
    snapshot_reconcile_hour: int = Field(ge=0, le=23)
    retention_cleanup_hour: int = Field(ge=0, le=23)
    # 0 = Watchdog aus. Nicht zu knapp waehlen (Default 10), damit ein nur
    # langsamer -- nicht haengender -- Schritt nicht vorzeitig hart
    # abgebrochen wird.
    backup_cancel_force_timeout_minutes: int = Field(ge=0, le=180)
    # 0 = keine Obergrenze (Default). Grosszuegig waehlen -- ein realer
    # Grosslauf darf nicht mittendrin abgeschnitten werden.
    backup_run_max_duration_minutes: int = Field(ge=0, le=1440)
    # 0 = automatisch (je Hyper-V-Host 1, ueber Hosts hinweg parallel),
    # 1 = nacheinander, N = hoechstens N Hosts gleichzeitig.
    backup_checkpoint_parallelism: int = Field(ge=0, le=64)
