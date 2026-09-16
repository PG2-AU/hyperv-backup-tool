from datetime import datetime

from pydantic import BaseModel


class CapacitySamplePoint(BaseModel):
    sampled_at: datetime
    capacity_bytes: int | None = None
    used_bytes: int | None = None


class CapacitySeries(BaseModel):
    object_key: str
    object_name: str
    points: list[CapacitySamplePoint]
