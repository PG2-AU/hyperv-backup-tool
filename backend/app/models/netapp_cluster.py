import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NetAppAuthMethod(str, enum.Enum):
    PASSWORD = "password"
    CERTIFICATE = "certificate"


class NetAppClusterHealth(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class NetAppCluster(Base):
    """Registrierter ONTAP-Cluster (unabhaengig davon, ob er Teil einer
    HA-/MetroCluster-Konfiguration ist -- das wird nach dem Hinzufuegen ueber
    die Cluster-API selbst erkannt, nicht beim Anlegen abgefragt)."""

    __tablename__ = "netapp_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    management_lif: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    auth_method: Mapped[NetAppAuthMethod] = mapped_column(Enum(NetAppAuthMethod), default=NetAppAuthMethod.PASSWORD)
    encrypted_password: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    client_cert_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_key_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)

    ontap_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ontap_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cluster_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    health: Mapped[NetAppClusterHealth] = mapped_column(Enum(NetAppClusterHealth), default=NetAppClusterHealth.UNKNOWN)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    healthy_node_count: Mapped[int] = mapped_column(Integer, default=0)
    is_metrocluster: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
