from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.netapp_cluster import NetAppAuthMethod, NetAppClusterHealth


class NetAppClusterCreate(BaseModel):
    name: str
    management_lif: str
    username: str
    password: str
    verify_ssl: bool = True


class DiscoveryStepRead(BaseModel):
    step: str
    success: bool
    message: str
    count: int | None = None


class NetAppClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    management_lif: str
    username: str
    auth_method: NetAppAuthMethod
    verify_ssl: bool
    ontap_version: str | None = None
    ontap_cluster_name: str | None = None
    cluster_uuid: str | None = None
    health: NetAppClusterHealth
    node_count: int
    healthy_node_count: int
    is_metrocluster: bool
    last_checked_at: datetime | None = None
    last_check_error: str | None = None
    created_at: datetime
