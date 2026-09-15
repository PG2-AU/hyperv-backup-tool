"""Settings > Kerberos: das eine, globale Realm/KDC-Paar fuer
HVNB_WINRM_TRANSPORT=kerberos (Backlog-Punkt 50).

Bewusst nur EIN Realm/KDC statt pro Cluster -- das Tool registriert laut
Nutzer-Vorgabe ohnehin nur Cluster innerhalb einer AD-Domaene, eine
Multi-Domain-Faehigkeit wurde explizit gestrichen. Singleton-Zeile,
gleiches Muster wie WinrmTrustState/EmailConfig. Das eigentliche
Schreiben der krb5.conf-Datei aus diesen Werten uebernimmt
app.core.kerberos_config (Modulname bewusst aehnlich, aber getrennt --
dort die reine Dateiverwaltung, hier nur die DB-Zeile)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KerberosConfig(Base):
    __tablename__ = "kerberos_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Kerberos-Realm, per Konvention GROSSGESCHRIEBEN (z.B. 'HYPERV.DEMO.AU.LOCAL').
    realm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # FQDN des Domain Controllers/KDC (z.B. 'svaudemo7-dc1.hyperv.demo.au.local').
    kdc_hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # IP-Adresse des KDC -- rein informativ/fuer die Diagnoseanzeige, die
    # eigentliche Verbindung erfolgt ueber kdc_hostname (DNS-Aufloesung
    # bereits live verifiziert, siehe [[credssp-checkpoint-auth-failures]]).
    kdc_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
