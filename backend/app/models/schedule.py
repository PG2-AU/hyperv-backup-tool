import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScheduleType(str, enum.Enum):
    HOURLY = "hourly"  # mehrere feste Uhrzeiten pro Tag (z.B. 08:30, 12:30, 16:30)
    DAILY = "daily"  # einmal taeglich zu einer festen Uhrzeit
    WEEKLY = "weekly"  # einmal woechentlich an einem festen Wochentag/Uhrzeit
    MONTHLY = "monthly"  # einmal monatlich an einem festen Tag/Uhrzeit


class Schedule(Base):
    """Wiederverwendbarer Zeitplan, der von mehreren Backup-Jobs referenziert
    werden kann. `times` enthaelt "HH:MM"-Strings: bei HOURLY beliebig viele
    Eintraege, bei DAILY/WEEKLY/MONTHLY genau einen."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    schedule_type: Mapped[ScheduleType] = mapped_column(Enum(ScheduleType))
    times: Mapped[list[str]] = mapped_column(JSON, default=list)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Montag..6=Sonntag, nur WEEKLY
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-31, nur MONTHLY
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
