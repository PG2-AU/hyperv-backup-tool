from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.backup_policy import BackupScope
from app.schemas.schedule import ScheduleRead


class PolicySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str


class ResourceGroupWrite(BaseModel):
    name: str
    scope: BackupScope
    members: list[str] = []
    policy_ids: list[str] = []
    # Siehe app.models.resource_group: der Zeitplan haengt an der Resource
    # Group, nicht an der Policy -- ermoeglicht, mehrere Gruppen mit
    # derselben Policy zeitversetzt statt gleichzeitig zu sichern.
    schedule_id: str | None = None


class ResourceGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    scope: BackupScope
    members: list[str]
    policies: list[PolicySummary]
    schedule_id: str | None = None
    schedule: ScheduleRead | None = None
    created_at: datetime
