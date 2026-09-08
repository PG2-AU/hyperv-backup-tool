from pydantic import BaseModel


class StorageAccessRead(BaseModel):
    actions_enabled: bool
    hide_metrocluster_mirrors: bool


class StorageAccessUpdate(BaseModel):
    actions_enabled: bool
    hide_metrocluster_mirrors: bool
