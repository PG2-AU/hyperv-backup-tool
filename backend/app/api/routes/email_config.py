"""Settings > E-Mail: SMTP-Konfiguration fuer das Alerting-Feature (Backup-/
Restore-Fehlschlaege, Tages-Zusammenfassung). Singleton-Zeile in der DB,
analog zum Muster in app.api.routes.settings/scheduler_status -- GUI-
verwaltet statt nur per ENV, damit ein Admin die Konfiguration ohne
Container-Neustart aendern kann."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.crypto import encrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.email_config import EmailConfig
from app.schemas.email_config import EmailConfigRead, EmailConfigUpdate, EmailTestRequest
from app.services.email_service import send_test_email

router = APIRouter(prefix="/api/email-config", tags=["email-config"])


def _get_or_create(db: Session) -> EmailConfig:
    config = db.query(EmailConfig).first()
    if config is None:
        config = EmailConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _to_read(config: EmailConfig) -> EmailConfigRead:
    data = EmailConfigRead.model_validate(config)
    data.has_password = bool(config.encrypted_password)
    return data


@router.get("", response_model=EmailConfigRead)
def get_email_config_route(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> EmailConfigRead:
    return _to_read(_get_or_create(db))


@router.put("", response_model=EmailConfigRead)
def update_email_config(
    payload: EmailConfigUpdate, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> EmailConfigRead:
    config = _get_or_create(db)
    config.enabled = payload.enabled
    config.smtp_host = payload.smtp_host
    config.smtp_port = payload.smtp_port
    config.smtp_encryption = payload.smtp_encryption
    config.smtp_username = payload.smtp_username
    if payload.smtp_password:
        config.encrypted_password = encrypt_secret(payload.smtp_password)
    config.from_address = payload.from_address
    config.from_name = payload.from_name
    config.recipients = payload.recipients
    config.notify_on_restore_failure = payload.notify_on_restore_failure
    config.daily_summary_enabled = payload.daily_summary_enabled
    config.daily_summary_hour = payload.daily_summary_hour
    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)
    return _to_read(config)


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
def send_test_email_route(
    payload: EmailTestRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> None:
    config = _get_or_create(db)
    if not config.smtp_host or not config.from_address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SMTP-Host und Absenderadresse muessen zuerst gespeichert werden")
    try:
        send_test_email(db, config, payload.recipient)
    except Exception as exc:
        config.last_test_at = datetime.now(timezone.utc)
        config.last_test_error = str(exc)[:2000]
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Test-Mail fehlgeschlagen: {exc}") from exc
    config.last_test_at = datetime.now(timezone.utc)
    config.last_test_error = None
    db.commit()
