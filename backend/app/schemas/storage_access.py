from pydantic import BaseModel


class StorageAccessRead(BaseModel):
    actions_enabled: bool


class StorageAccessUpdate(BaseModel):
    actions_enabled: bool
