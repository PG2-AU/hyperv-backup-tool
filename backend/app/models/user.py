import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime


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

    @property
    def global_role_assignment(self):
        """Erste Rollenzuweisung mit scope_type='global'. Scoping (VM-/CSV-/
        Host-Ebene) ist aktuell ohnehin wirkungslos (siehe get_user_
        permissions in app.api.deps) und die GUI bietet ausschliesslich
        globale Zuweisungen an -- ein Benutzer hat also praktisch hoechstens
        eine Zuweisung, dieses Property macht sie fuer UserRead.role_id/
        role_name (schemas/user.py) direkt lesbar."""
        return next((a for a in self.role_assignments if a.scope_type == "global"), None)

    @property
    def role_id(self) -> str | None:
        assignment = self.global_role_assignment
        return assignment.role_id if assignment else None

    @property
    def role_name(self) -> str | None:
        assignment = self.global_role_assignment
        return assignment.role.name if assignment and assignment.role else None
