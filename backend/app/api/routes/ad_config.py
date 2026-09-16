"""Settings > Active Directory: GUI-verwaltete AD-Integration fuer die
GUI-Anmeldung (nicht zu verwechseln mit Kerberos fuer WinRM, siehe
app.api.routes.kerberos_config -- zwei getrennte Subsysteme).

Ersetzt die bisherigen Settings.ad_*-Felder (.env-only) vollstaendig.
"Verbindung testen" bindet mit dem im Formular eingegebenen
Service-Konto, ohne zu speichern -- gleiches Muster wie beim
Kerberos-Verbindungstest."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.ad_config import AdConfig
from app.schemas.ad_config import AdConfigRead, AdConfigWrite, AdTestRequest, AdTestResult
from app.services.ad_service import ActiveDirectoryService

router = APIRouter(prefix="/api/ad-config", tags=["ad-config"])


def _get_or_create_config(db: Session) -> AdConfig:
    config = db.query(AdConfig).first()
    if config is None:
        config = AdConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _to_read(config: AdConfig) -> AdConfigRead:
    return AdConfigRead(
        enabled=config.enabled,
        server=config.server,
        domain=config.domain,
        base_dn=config.base_dn,
        use_ssl=config.use_ssl,
        bind_user=config.bind_user,
        bind_password_set=bool(config.encrypted_bind_password),
        updated_at=config.updated_at,
        updated_by=config.updated_by,
    )


@router.get("", response_model=AdConfigRead)
def get_config(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> AdConfigRead:
    return _to_read(_get_or_create_config(db))


@router.put("", response_model=AdConfigRead)
def update_config(
    payload: AdConfigWrite, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> AdConfigRead:
    config = _get_or_create_config(db)
    config.enabled = payload.enabled
    config.server = payload.server.strip()
    config.domain = payload.domain.strip()
    config.base_dn = payload.base_dn.strip()
    config.use_ssl = payload.use_ssl
    config.bind_user = payload.bind_user.strip()
    if payload.bind_password:
        config.encrypted_bind_password = encrypt_secret(payload.bind_password)
    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = getattr(user, "username", None)
    db.commit()
    db.refresh(config)
    return _to_read(config)


@router.post("/test", response_model=AdTestResult)
def test_connection(
    payload: AdTestRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> AdTestResult:
    """Bindet mit dem im Formular stehenden (noch nicht zwingend
    gespeicherten) Service-Konto gegen den Server -- speichert nichts.
    Ein leeres bind_password faellt auf das bereits gespeicherte
    Passwort zurueck (analog zu 'Speichern')."""
    bind_password = payload.bind_password
    if not bind_password:
        existing = db.query(AdConfig).first()
        if existing is not None and existing.encrypted_bind_password:
            bind_password = decrypt_secret(existing.encrypted_bind_password)
    if not payload.bind_user or not bind_password:
        return AdTestResult(success=False, message="Service-Konto (Benutzername + Passwort) fehlt")

    service = ActiveDirectoryService(payload.server, payload.domain, payload.base_dn, payload.use_ssl)
    result = service.search_users(payload.bind_user, bind_password, payload.bind_user)
    if not result.success:
        return AdTestResult(success=False, message=result.error or "Verbindung fehlgeschlagen")
    return AdTestResult(success=True, message="Verbindung erfolgreich -- Service-Konto kann das Verzeichnis durchsuchen")
