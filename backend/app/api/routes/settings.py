"""Liefert die nicht-sensitive Server-Konfiguration fuer die Einstellungen-Seite.

TODO(iteration): Schreibender Zugriff (Speichern aus der GUI) folgt, sobald
die Konfiguration statt ENV/.env auch in der DB verwaltet werden kann.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_permission
from app.core.config import get_settings
from app.core.rbac import Permission
from app.schemas.settings import PublicSettings, VersionInfo

router = APIRouter(prefix="/api/settings", tags=["settings"])

APP_DIR = Path("/opt/app")


@router.get("", response_model=PublicSettings)
def get_public_settings(user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> PublicSettings:
    settings = get_settings()
    return PublicSettings(**{field: getattr(settings, field) for field in PublicSettings.model_fields})


@router.get("/version", response_model=VersionInfo)
def get_version(user=Depends(get_current_user)) -> VersionInfo:
    """Aktueller Git-Commit (entrypoint.sh/updater.sh checken den Branch per
    'git reset --hard' aus, siehe docker/) sowie Zeitpunkt des letzten
    Deploys -- abgeleitet aus dem Zeitstempel des zuletzt gebauten
    Frontend-Bundles, da dieses bei jedem entrypoint- oder Updater-Lauf frisch
    geschrieben wird und damit den tatsaechlichen Deploy-Zeitpunkt genauer
    abbildet als der Commit-Zeitstempel selbst (Commit und Deploy koennen
    zeitlich auseinanderfallen)."""
    commit: str | None = None
    try:
        result = subprocess.run(
            ["git", "-C", str(APP_DIR), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
    except Exception:
        commit = None

    last_deploy_at: str | None = None
    dist_marker = APP_DIR / "frontend" / "dist" / "index.html"
    try:
        if dist_marker.exists():
            mtime = dist_marker.stat().st_mtime
            last_deploy_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        last_deploy_at = None

    return VersionInfo(commit=commit, commit_short=commit[:7] if commit else None, last_deploy_at=last_deploy_at)
