"""Liefert die nicht-sensitive Server-Konfiguration fuer die Einstellungen-Seite.

TODO(iteration): Schreibender Zugriff (Speichern aus der GUI) folgt, sobald
die Konfiguration statt ENV/.env auch in der DB verwaltet werden kann.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permission
from app.core.config import get_settings
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.scheduler_status import SchedulerStatus
from app.schemas.settings import PublicSettings, VersionInfo

router = APIRouter(prefix="/api/settings", tags=["settings"])

APP_DIR = Path("/opt/app")


@router.get("", response_model=PublicSettings)
def get_public_settings(user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> PublicSettings:
    settings = get_settings()
    return PublicSettings(**{field: getattr(settings, field) for field in PublicSettings.model_fields})


@router.get("/version", response_model=VersionInfo)
def get_version(db: Session = Depends(get_db), user=Depends(get_current_user)) -> VersionInfo:
    """Aktueller Git-Commit (entrypoint.sh/updater.sh checken den Branch per
    'git reset --hard' aus, siehe docker/) sowie Zeitpunkt des letzten
    Deploys -- abgeleitet aus dem Zeitstempel des zuletzt gebauten
    Frontend-Bundles, da dieses bei jedem entrypoint- oder Updater-Lauf frisch
    geschrieben wird und damit den tatsaechlichen Deploy-Zeitpunkt genauer
    abbildet als der Commit-Zeitstempel selbst (Commit und Deploy koennen
    zeitlich auseinanderfallen). commit_count ('Iteration') ist schlicht die
    Gesamtzahl der Commits im Repo -- eine einfache, automatisch mitlaufende
    Kennzahl ohne manuelle Pflege. Die drei last_*-Zeitstempel der
    Hintergrund-Jobs kommen aus der SchedulerStatus-Singleton-Zeile (siehe
    app.core.scheduler), die bei jedem Lauf aktualisiert wird."""
    commit: str | None = None
    commit_count: int | None = None
    try:
        result = subprocess.run(
            ["git", "-C", str(APP_DIR), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
        count_result = subprocess.run(
            ["git", "-C", str(APP_DIR), "rev-list", "--count", "HEAD"], capture_output=True, text=True, timeout=5,
        )
        if count_result.returncode == 0:
            commit_count = int(count_result.stdout.strip())
    except Exception:
        pass

    last_deploy_at: str | None = None
    dist_marker = APP_DIR / "frontend" / "dist" / "index.html"
    try:
        if dist_marker.exists():
            mtime = dist_marker.stat().st_mtime
            last_deploy_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        last_deploy_at = None

    scheduler_status = db.query(SchedulerStatus).first()

    return VersionInfo(
        commit=commit,
        commit_short=commit[:7] if commit else None,
        commit_count=commit_count,
        last_deploy_at=last_deploy_at,
        last_health_check_at=scheduler_status.last_health_check_at.isoformat() if scheduler_status and scheduler_status.last_health_check_at else None,
        last_discovery_at=scheduler_status.last_discovery_at.isoformat() if scheduler_status and scheduler_status.last_discovery_at else None,
        last_snapshot_reconciliation_at=(
            scheduler_status.last_snapshot_reconciliation_at.isoformat()
            if scheduler_status and scheduler_status.last_snapshot_reconciliation_at
            else None
        ),
    )
