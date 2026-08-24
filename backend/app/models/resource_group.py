import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.backup_policy import BackupScope

resource_group_policies = Table(
    "resource_group_policies",
    Base.metadata,
    Column("resource_group_id", String(36), ForeignKey("resource_groups.id"), primary_key=True),
    Column("policy_id", String(36), ForeignKey("backup_policies.id"), primary_key=True),
)


class ResourceGroup(Base):
    """Buendelt VMs oder CSVs (nie gemischt, siehe `scope`) zu einer benannten
    Gruppe (z.B. 'Bronze'), die mit einer oder mehreren Backup-Policies
    verknuepft wird. Die Kombination aus Resource Group + Policy ergibt die
    tatsaechliche Backup-Definition (was wird wann/wie gesichert)."""

    __tablename__ = "resource_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    scope: Mapped[BackupScope] = mapped_column(Enum(BackupScope))
    members: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    policies = relationship("BackupPolicy", secondary=resource_group_policies, back_populates="resource_groups")
