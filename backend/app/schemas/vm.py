from pydantic import BaseModel


class VmRead(BaseModel):
    id: str
    name: str
    state: str
    host: str
    cluster: str | None = None
    csv_paths: list[str] = []


class CsvRead(BaseModel):
    name: str
    owner_node: str
    state: str
    volume_path: str
