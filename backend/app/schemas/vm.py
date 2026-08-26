from pydantic import BaseModel


class VhdInfo(BaseModel):
    name: str
    size_bytes: int
    csv_path: str
    full_path: str


class VmRead(BaseModel):
    id: str
    name: str
    state: str
    host: str
    cluster: str | None = None
    csv_paths: list[str] = []
    vhdx_size_bytes: int | None = None
    vhds: list[VhdInfo] = []
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    policy_ids: list[str] = []
    protected: bool = False


class CsvRead(BaseModel):
    name: str
    owner_node: str
    state: str
    volume_path: str
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    lun_name: str | None = None
    lun_capacity_bytes: int | None = None
    lun_used_bytes: int | None = None
    volume_name: str | None = None
    volume_capacity_bytes: int | None = None
    volume_used_bytes: int | None = None
    svm_name: str | None = None
    netapp_cluster_name: str | None = None
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    policy_ids: list[str] = []
    protected: bool = False
