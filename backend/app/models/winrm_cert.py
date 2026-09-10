"""Settings > WinRM-Zertifikate: pro Hyper-V-Knoten hochgeladenes
WinRM-HTTPS-Zertifikat plus der Zustand des daraus erzeugten CA-Trust-
Bundles.

Ersetzt den bisher rein manuellen Ablauf aus DEPLOYMENT.md Kapitel 10
(pro Knoten exportieren -> von Hand zu einer PEM buendeln -> podman cp ->
.env -> Neustart). Die einzelnen Zertifikate liegen als Zeilen in
`winrm_host_certificates`, das konkatenierte Bundle schreibt der
`build-bundle`-Endpunkt an den von `app.core.winrm_trust.bundle_write_target`
bestimmten Pfad -- ohne Neustart, siehe dortiges Modul.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WinrmHostCertificate(Base):
    __tablename__ = "winrm_host_certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Anzeigename (Default = geparster Common Name), frei ueberschreibbar.
    label: Mapped[str] = mapped_column(String(255))
    # Vom Nutzer bestaetigte Management-IP des Knotens (Default = erste
    # IP-Address-SAN des Zertifikats) -- rein informativ fuer die Tabelle.
    host_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Normalisierter Einzel-Zertifikats-PEM (genau ein CERTIFICATE-Block).
    pem: Mapped[str] = mapped_column(Text)
    # SHA-256-Fingerprint (hex, lowercase, ohne Trenner) -- Dedupe-Schluessel.
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    subject_cn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Liste der SAN-Eintraege als Klartext, z.B. ["DNS:host01.example.local",
    # "IP Address:10.10.2.11"].
    sans: Mapped[list | None] = mapped_column(JSON, nullable=True)
    not_before: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    not_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WinrmTrustState(Base):
    """Singleton-Zeile: Zustand des zuletzt geschriebenen Bundles (fuer die
    Status-Anzeige in der GUI)."""

    __tablename__ = "winrm_trust_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    last_bundle_built_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bundle_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    bundle_cert_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
