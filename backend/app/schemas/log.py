from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogEntry(BaseModel):
    timestamp: datetime
    level: LogLevel
    source: str
    context: str | None = None
    message: str
