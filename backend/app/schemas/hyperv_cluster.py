from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.hyperv_cluster import HyperVClusterHealth


class HyperVReachabilityCheck(BaseModel):
    management_address: str


class HyperVClusterCreate(BaseModel):
    name: str
    management_address: str
    username: str
    password: str


class HyperVClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    management_address: str
    username: str
    hyperv_cluster_name: str | None = None
    health: HyperVClusterHealth
    node_count: int
    healthy_node_count: int
    last_checked_at: datetime | None = None
    last_check_error: str | None = None
    created_at: datetime
