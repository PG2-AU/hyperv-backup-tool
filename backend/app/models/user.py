import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserSource(str, enum.Enum):
    LOCAL = "local"
    ACTIVE_DIRECTORY = "active_directory"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[UserSource] = mapped_column(Enum(UserSource), default=UserSource.LOCAL)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    role_assignments = relationship("RoleAssignment", back_populates="user", cascade="all, delete-orphan")
