"""Liefert die nicht-sensitive Server-Konfiguration fuer die Einstellungen-Seite.

TODO(iteration): Schreibender Zugriff (Speichern aus der GUI) folgt, sobald
die Konfiguration statt ENV/.env auch in der DB verwaltet werden kann.
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.rbac import Permission
from app.schemas.settings import PublicSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=PublicSettings)
def get_public_settings(user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> PublicSettings:
    settings = get_settings()
    return PublicSettings(**{field: getattr(settings, field) for field in PublicSettings.model_fields})
