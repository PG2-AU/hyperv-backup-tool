from pydantic import BaseModel


class VmRead(BaseModel):
    id: str
    name: str
    state: str
    host: str
    cluster: str | None = None
    csv_paths: list[str] = []
    vhdx_size_bytes: int | None = None
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    protected: bool = False


class CsvRead(BaseModel):
    name: str
    owner_node: str
    state: str
    volume_path: str
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    lun_name: str | None = None
    volume_name: str | None = None
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    protected: bool = False
