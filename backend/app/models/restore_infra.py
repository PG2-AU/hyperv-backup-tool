"""Vom Restore-Setup-Wizard angelegte iSCSI-Restore-Infrastruktur pro
NetApp-Cluster/SVM (welches Interface, welche Igroup, welcher Initiator) --
damit ein spaeterer Restore-Lauf diese nicht jedes Mal neu ermitteln muss."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RestoreInfraConfig(Base):
    __tablename__ = "restore_infra_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    netapp_cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("netapp_clusters.id", ondelete="CASCADE"))
    svm_name: Mapped[str] = mapped_column(String(255))
    iscsi_lif_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    iscsi_lif_address: Mapped[str] = mapped_column(String(100))
    iscsi_lif_port: Mapped[int] = mapped_column(Integer, default=3260)
    igroup_name: Mapped[str] = mapped_column(String(255))
    initiator_iqn: Mapped[str] = mapped_column(String(255))
    configured_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
