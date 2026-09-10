"""Settings > WinRM-Zertifikate: Assistent (PowerShell-Skript erzeugen),
Upload der pro Hyper-V-Knoten exportierten WinRM-HTTPS-Zertifikate und
Erzeugen des daraus konkatenierten CA-Trust-Bundles.

Ersetzt den manuellen Ablauf aus DEPLOYMENT.md Kapitel 10 (von Hand
buendeln -> podman cp -> .env -> Neustart). Das Bundle wird an den von
app.core.winrm_trust.bundle_write_target bestimmten Pfad geschrieben und
vom WinRM-Code ohne Neustart uebernommen (siehe dortiges Modul).
"""

import os
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.rbac import Permission
from app.core.winrm_setup_script import build_setup_script
from app.core.winrm_trust import bundle_write_target, resolved_trust_path
from app.db.session import get_db
from app.models.winrm_cert import WinrmHostCertificate, WinrmTrustState
from app.schemas.winrm_cert import (
    SetupScriptRequest,
    SetupScriptResponse,
    WinrmCertsOverviewRead,
    WinrmHostCertificateRead,
    WinrmTrustStateRead,
)

router = APIRouter(prefix="/api/winrm-certs", tags=["winrm-certs"])

_MAX_UPLOAD_BYTES = 256 * 1024


def _get_or_create_state(db: Session) -> WinrmTrustState:
    state = db.query(WinrmTrustState).first()
    if state is None:
        state = WinrmTrustState()
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_certificates(raw: bytes) -> list[x509.Certificate]:
    """Nimmt PEM (ein oder mehrere CERTIFICATE-Bloecke) oder DER entgegen."""
    if b"PRIVATE KEY" in raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Die Datei enthaelt einen privaten Schluessel -- bitte nur das oeffentliche Zertifikat (.cer/.pem) hochladen.",
        )
    try:
        certs = x509.load_pem_x509_certificates(raw)
        if certs:
            return certs
    except Exception:  # noqa: BLE001 -- kein PEM, unten DER versuchen
        pass
    try:
        return [x509.load_der_x509_certificate(raw)]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Konnte kein X.509-Zertifikat aus der Datei lesen: {exc}",
        ) from exc


def _common_name(cert: x509.Certificate) -> str | None:
    attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else None


def _sans(cert: x509.Certificate) -> list[str]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    out = [f"DNS:{v}" for v in ext.get_values_for_type(x509.DNSName)]
    out += [f"IP Address:{v}" for v in ext.get_values_for_type(x509.IPAddress)]
    return out


def _first_ip_san(sans: list[str]) -> str | None:
    for entry in sans:
        if entry.startswith("IP Address:"):
            return entry.split(":", 1)[1]
    return None


def _overview(db: Session) -> WinrmCertsOverviewRead:
    settings = get_settings()
    certs = db.query(WinrmHostCertificate).order_by(WinrmHostCertificate.label).all()
    state = _get_or_create_state(db)
    built_at = _utc(state.last_bundle_built_at)
    outdated = bool(certs) and (
        built_at is None or any((_utc(c.uploaded_at) or built_at) > built_at for c in certs)
    )
    return WinrmCertsOverviewRead(
        certificates=[WinrmHostCertificateRead.model_validate(c) for c in certs],
        trust_state=WinrmTrustStateRead.model_validate(state),
        active_trust_path=resolved_trust_path(settings),
        bundle_outdated=outdated,
    )


@router.get("", response_model=WinrmCertsOverviewRead)
def get_overview(
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> WinrmCertsOverviewRead:
    return _overview(db)


@router.post("", response_model=WinrmCertsOverviewRead)
async def upload_certificate(
    file: UploadFile = File(...),
    label: str | None = Form(None),
    host_address: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> WinrmCertsOverviewRead:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Leere Datei.")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Datei ist zu gross (max. 256 KiB).")

    now = datetime.now(timezone.utc)
    for cert in _parse_certificates(raw):
        fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        cn = _common_name(cert)
        sans = _sans(cert)
        pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        row = db.query(WinrmHostCertificate).filter(WinrmHostCertificate.fingerprint_sha256 == fingerprint).first()
        if row is None:
            row = WinrmHostCertificate(fingerprint_sha256=fingerprint)
            db.add(row)
        row.label = (label or "").strip() or cn or fingerprint[:16]
        row.host_address = (host_address or "").strip() or _first_ip_san(sans)
        row.pem = pem
        row.subject_cn = cn
        row.sans = sans
        row.not_before = _utc(getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before)
        row.not_after = _utc(getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after)
        row.uploaded_at = now
        row.uploaded_by = getattr(user, "username", None)
    db.commit()
    return _overview(db)


@router.delete("/{cert_id}", response_model=WinrmCertsOverviewRead)
def delete_certificate(
    cert_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> WinrmCertsOverviewRead:
    row = db.get(WinrmHostCertificate, cert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zertifikat nicht gefunden.")
    db.delete(row)
    db.commit()
    return _overview(db)


@router.post("/build-bundle", response_model=WinrmCertsOverviewRead)
def build_bundle(
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> WinrmCertsOverviewRead:
    settings = get_settings()
    certs = db.query(WinrmHostCertificate).order_by(WinrmHostCertificate.label).all()
    if not certs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Es sind keine Zertifikate hinterlegt -- zuerst mindestens eines hochladen.",
        )
    body = "".join(c.pem if c.pem.endswith("\n") else c.pem + "\n" for c in certs)
    target = bundle_write_target(settings)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / (target.name + ".tmp")
        tmp.write_text(body, encoding="ascii")
        os.replace(tmp, target)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Bundle-Pfad {target} ist nicht beschreibbar ({exc}). "
                "HVNB_WINRM_CA_TRUST_PATH auf einen beschreibbaren Pfad wie "
                "/data/winrm-trust/bundle.pem setzen (oder leer lassen)."
            ),
        ) from exc

    state = _get_or_create_state(db)
    state.last_bundle_built_at = datetime.now(timezone.utc)
    state.bundle_path = str(target)
    state.bundle_cert_count = len(certs)
    state.updated_by = getattr(user, "username", None)
    db.commit()
    return _overview(db)


@router.post("/setup-script", response_model=SetupScriptResponse)
def generate_setup_script(
    payload: SetupScriptRequest,
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> SetupScriptResponse:
    if payload.cluster_type == "failover_cluster" and not ((payload.cno_hostname or "").strip() and (payload.cno_ip or "").strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fuer einen Failover-Cluster sind Cluster-DNS-Name und Cluster-IP erforderlich.",
        )
    return SetupScriptResponse(script=build_setup_script(payload))
