import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HyperVClusterHealth(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class HyperVCluster(Base):
    """Registrierter Hyper-V-Failover-Cluster. Registriert wird der Cluster
    selbst (ueber seinen Cluster Name Object / Management-IP), nicht die
    einzelnen Knoten -- WinRM/PowerShell-Remoting an den Cluster-Namen
    routet transparent zum jeweils aktiven Knoten. Die Mitgliedsknoten
    werden (analog zu NetApp SVMs/Nodes) separat discovert."""

    __tablename__ = "hyperv_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    management_address: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    hyperv_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health: Mapped[HyperVClusterHealth] = mapped_column(Enum(HyperVClusterHealth), default=HyperVClusterHealth.UNKNOWN)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    healthy_node_count: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
