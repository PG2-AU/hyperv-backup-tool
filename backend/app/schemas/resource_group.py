from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.backup_policy import BackupScope


class PolicySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str


class ResourceGroupWrite(BaseModel):
    name: str
    scope: BackupScope
    members: list[str] = []
    policy_ids: list[str] = []


class ResourceGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    scope: BackupScope
    members: list[str]
    policies: list[PolicySummary]
    created_at: datetime
