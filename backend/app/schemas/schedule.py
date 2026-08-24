import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.schedule import ScheduleType

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleWrite(BaseModel):
    """Gemeinsames Payload-Schema fuer Anlegen und Bearbeiten eines Zeitplans."""

    name: str
    schedule_type: ScheduleType
    times: list[str]
    weekday: int | None = None  # 0=Montag..6=Sonntag, nur bei WEEKLY
    day_of_month: int | None = None  # 1-31, nur bei MONTHLY

    @model_validator(mode="after")
    def _validate(self) -> "ScheduleWrite":
        if not self.times:
            raise ValueError("Mindestens eine Uhrzeit erforderlich")
        for t in self.times:
            if not _TIME_RE.match(t):
                raise ValueError(f"Ungueltige Uhrzeit '{t}' (erwartet HH:MM)")

        if self.schedule_type != ScheduleType.HOURLY and len(self.times) != 1:
            raise ValueError(f"Zeitplan-Typ '{self.schedule_type.value}' erlaubt genau eine Uhrzeit")

        if self.schedule_type == ScheduleType.WEEKLY:
            if self.weekday is None or not (0 <= self.weekday <= 6):
                raise ValueError("Wochentag (0=Montag..6=Sonntag) erforderlich fuer woechentliche Zeitplaene")
        elif self.weekday is not None:
            raise ValueError("Wochentag ist nur fuer woechentliche Zeitplaene zulaessig")

        if self.schedule_type == ScheduleType.MONTHLY:
            if self.day_of_month is None or not (1 <= self.day_of_month <= 31):
                raise ValueError("Tag des Monats (1-31) erforderlich fuer monatliche Zeitplaene")
        elif self.day_of_month is not None:
            raise ValueError("Tag des Monats ist nur fuer monatliche Zeitplaene zulaessig")

        return self


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    schedule_type: ScheduleType
    times: list[str]
    weekday: int | None = None
    day_of_month: int | None = None
    created_at: datetime
