import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Role(Base):
    """Rolle mit einer Menge von Permission-Strings (siehe app.core.rbac.Permission)."""

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_system_role: Mapped[bool] = mapped_column(default=False)

    assignments = relationship("RoleAssignment", back_populates="role", cascade="all, delete-orphan")


class RoleAssignment(Base):
    """Verknuepft einen User mit einer Rolle, optional eingeschraenkt auf
    einen Scope (z.B. bestimmte VMs, CSVs, LUNs oder Hyper-V-Hosts).
    scope_type = "global" bedeutet: Rolle gilt ueberall.
    """

    __tablename__ = "role_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id"))
    scope_type: Mapped[str] = mapped_column(String(50), default="global")
    scope_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="role_assignments")
    role = relationship("Role", back_populates="assignments")
