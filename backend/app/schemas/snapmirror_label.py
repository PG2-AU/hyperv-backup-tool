from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SnapMirrorLabelWrite(BaseModel):
    name: str


class SnapMirrorLabelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime
