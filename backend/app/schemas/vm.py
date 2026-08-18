from pydantic import BaseModel


class VmRead(BaseModel):
    id: str
    name: str
    state: str
    host: str
    cluster: str | None = None
    csv_paths: list[str] = []
    vhdx_size_bytes: int | None = None
    backup_policy_id: str | None = None
    backup_policy_name: str | None = None


class CsvRead(BaseModel):
    name: str
    owner_node: str
    state: str
    volume_path: str
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    backup_policy_id: str | None = None
    backup_policy_name: str | None = None
