from pydantic import BaseModel


class SvmInfo(BaseModel):
    name: str
    state: str
    is_metrocluster: bool = False


class SnapMirrorRelationshipRead(BaseModel):
    uuid: str
    source_path: str
    destination_path: str
    state: str
    healthy: bool


class MetroClusterStatus(BaseModel):
    configured: bool
    mode: str
    switchover_in_progress: bool
