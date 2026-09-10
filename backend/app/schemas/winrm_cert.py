from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class WinrmHostCertificateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    host_address: str | None = None
    fingerprint_sha256: str
    subject_cn: str | None = None
    sans: list[str] = []
    not_before: datetime | None = None
    not_after: datetime | None = None
    uploaded_at: datetime
    uploaded_by: str | None = None


class WinrmTrustStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    last_bundle_built_at: datetime | None = None
    bundle_path: str | None = None
    bundle_cert_count: int = 0
    updated_by: str | None = None


class WinrmCertsOverviewRead(BaseModel):
    certificates: list[WinrmHostCertificateRead]
    trust_state: WinrmTrustStateRead
    # Pfad, den der WinRM-Code aktuell tatsaechlich als ca_trust_path nutzt
    # (explizites ENV, verwaltetes Bundle, oder "legacy_requests" = nur
    # System-Truststore) -- siehe app.core.winrm_trust.resolved_trust_path.
    active_trust_path: str
    # Seit dem letzten Bundle-Schreiben wurden Zertifikate hinzugefuegt/
    # geaendert -> das Bundle ist nicht mehr aktuell.
    bundle_outdated: bool


class SetupScriptRequest(BaseModel):
    cluster_type: Literal["failover_cluster", "single_host"] = "failover_cluster"
    cno_hostname: str | None = None
    cno_ip: str | None = None
    own_ip: str | None = None


class SetupScriptResponse(BaseModel):
    script: str
