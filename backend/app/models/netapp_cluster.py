import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class NetAppAuthMethod(str, enum.Enum):
    PASSWORD = "password"
    CERTIFICATE = "certificate"


class NetAppClusterHealth(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class NetAppSystemType(str, enum.Enum):
    """Ob dieses registrierte Storage-System der gesamte ONTAP-Cluster
    (Cluster-Admin-Zugangsdaten, volle Sicht auf Nodes/Aggregate/Cluster-
    Peers/MetroCluster) oder nur eine einzelne SVM ist (vsadmin-Zugangsdaten,
    an genau eine SVM gebunden -- ONTAPs eigenes RBAC beschraenkt die Sicht
    serverseitig automatisch auf die Objekte dieser SVM, die App muss dafuer
    keinen eigenen Filter bauen). Wird beim Hinzufuegen EINMALIG gewaehlt und
    ist danach nicht mehr aenderbar (siehe NetAppClusterUpdate) -- ein
    nachtraeglicher Typwechsel wuerde verwaiste Nodes/Aggregate/Cluster-Peer-
    Zeilen aus der vorherigen Discovery hinterlassen."""

    CLUSTER = "cluster"
    SVM = "svm"


class NetAppCluster(Base):
    """Registriertes ONTAP-Storage-System -- entweder ein ganzer Cluster
    (unabhaengig davon, ob er Teil einer HA-/MetroCluster-Konfiguration ist
    -- das wird nach dem Hinzufuegen ueber die Cluster-API selbst erkannt,
    nicht beim Anlegen abgefragt) oder eine einzelne SVM (siehe
    NetAppSystemType)."""

    __tablename__ = "netapp_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    system_type: Mapped[NetAppSystemType] = mapped_column(Enum(NetAppSystemType), default=NetAppSystemType.CLUSTER)
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
