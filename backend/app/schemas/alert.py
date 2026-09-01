from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AlertRead(BaseModel):
    id: str
    alert_type: str
    object_name: str
    netapp_cluster_id: str | None = None
    netapp_cluster_name: str | None = None
    hyperv_cluster_id: str | None = None
    svm_name: str | None = None
    message: str
    threshold_percent: int | None = None
    triggered_percent: int | None = None
    status: str
    triggered_at: datetime
    resolved_at: datetime | None = None
    # Fuer den Redirect zur passenden Aktion (nur bei capacity_volume/-lun
    # bzw. backup_failed gesetzt, siehe list_alerts in routes/alerts.py):
    object_uuid: str | None = None
    run_id: str | None = None


class AlertConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    volume_threshold_percent: int
    lun_threshold_percent: int
    snapmirror_lag_threshold_minutes: int
    scope: str


class AlertConfigUpdate(BaseModel):
    volume_threshold_percent: int = Field(ge=1, le=100)
    lun_threshold_percent: int = Field(ge=1, le=100)
    snapmirror_lag_threshold_minutes: int = Field(ge=1, le=100_000)
    scope: str
