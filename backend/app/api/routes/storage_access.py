"""Globaler Sicherheits-Schalter fuer alle Storage-Aktionen -- siehe
app.models.storage_access.StorageAccessConfig fuer den Hintergrund. Nur
diese zwei Routen: der Lesezugriff (fuer die Storage-Seite, die den
Schalter braucht um Aktions-Buttons auszugrauen) ist bereits mit
STORAGE_VIEW moeglich, das Umschalten selbst bleibt Admins vorbehalten
(SETTINGS_MANAGE) -- eigener Router statt Anhaengen an das generische
GET /api/settings, das komplett SETTINGS_MANAGE voraussetzt und damit fuer
Nicht-Admins mit reinem Storage-Blick nicht lesbar waere."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.storage_access import StorageAccessConfig
from app.schemas.storage_access import StorageAccessRead, StorageAccessUpdate

router = APIRouter(prefix="/api/storage-access", tags=["storage-access"])


def _get_or_create(db: Session) -> StorageAccessConfig:
    config = db.query(StorageAccessConfig).first()
    if config is None:
        config = StorageAccessConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.get("", response_model=StorageAccessRead)
def get_storage_access(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW)),
) -> StorageAccessConfig:
    return _get_or_create(db)


@router.put("", response_model=StorageAccessRead)
def update_storage_access(
    payload: StorageAccessUpdate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> StorageAccessConfig:
    config = _get_or_create(db)
    config.actions_enabled = payload.actions_enabled
    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)
    return config
