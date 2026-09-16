"""Settings > Active Directory: GUI-verwaltete AD-Integration fuer die
GUI-Anmeldung lokaler Benutzerkonten (nicht zu verwechseln mit Kerberos
fuer die WinRM-Verbindung zu Hyper-V, siehe app.core.kerberos_config --
zwei getrennte Subsysteme).

Singleton-Zeile, gleiches Muster wie KerberosConfig/WinrmTrustState.
Ersetzt die bisherigen `.env`-only Settings.ad_*-Felder vollstaendig --
Aenderungen wirken ohne Container-Neustart, da jeder Login-/Such-Request
ohnehin schon eine DB-Session hat."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AdConfig(Base):
    __tablename__ = "ad_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    server: Mapped[str] = mapped_column(String(255), default="")
    domain: Mapped[str] = mapped_column(String(255), default="")
    base_dn: Mapped[str] = mapped_column(String(500), default="")
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    # Lese-Service-Konto NUR fuer die Verzeichnis-Suche (Settings >
    # Benutzer & Rollen > "Benutzer hinzufuegen" > Active Directory) --
    # der normale Login bindet weiterhin direkt als der anzumeldende
    # Nutzer selbst (siehe ActiveDirectoryService.authenticate), braucht
    # dieses Konto nicht.
    bind_user: Mapped[str] = mapped_column(String(255), default="")
    encrypted_bind_password: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
