from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.backup_policy import BackupScope
from app.schemas.schedule import ScheduleRead


class PolicySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str


class ResourceGroupPolicyLinkWrite(BaseModel):
    """Eine verknuepfte Policy samt ihrem eigenen Zeitplan (siehe
    app.models.resource_group.ResourceGroupPolicyLink) -- ersetzt das
    fruehere reine policy_ids: list[str], da derselbe Zeitplan nicht mehr
    fuer alle Verknuepfungen einer Resource Group gilt."""

    policy_id: str
    schedule_id: str | None = None


class ResourceGroupWrite(BaseModel):
    name: str
    scope: BackupScope
    members: list[str] = []
    policy_links: list[ResourceGroupPolicyLinkWrite] = []


class ResourceGroupPolicyLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    policy_id: str
    policy_name: str
    schedule_id: str | None = None
    schedule: ScheduleRead | None = None


class ResourceGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    scope: BackupScope
    members: list[str]
    policies: list[PolicySummary]
    policy_links: list[ResourceGroupPolicyLinkRead]
    created_at: datetime
