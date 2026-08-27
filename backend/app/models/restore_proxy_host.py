"""Restore-Proxy-Host: dedizierter Windows-Host mit nativem iSCSI-Initiator,
den der VHDX-Restore-Workflow per WinRM fuer LUN-Mount + Kopie nutzt. Es gibt
genau eine Konfiguration fuer die gesamte Installation (Singleton-Tabelle,
im Restore-Setup-Wizard gepflegt statt ueber HVNB_RESTORE_PROXY_*-Env-Vars,
damit Aenderungen ohne Container-Neustart wirksam werden)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RestoreProxyHost(Base):
    __tablename__ = "restore_proxy_host"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    address: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    use_https: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
