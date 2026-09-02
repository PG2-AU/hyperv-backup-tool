"""Liefert die nicht-sensitive Server-Konfiguration fuer die Einstellungen-Seite.

TODO(iteration): Schreibender Zugriff (Speichern aus der GUI) folgt, sobald
die Konfiguration statt ENV/.env auch in der DB verwaltet werden kann.
"""

import re
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
from app.schemas.settings import CommitInfo, PublicSettings, VersionInfo

router = APIRouter(prefix="/api/settings", tags=["settings"])

APP_DIR = Path("/opt/app")


def _redact_git_url(url: str) -> str:
    """Entfernt in einer HTTPS-Git-URL eingebettete Zugangsdaten
    (https://user:token@host/...), bevor sie ueber diesen (als
    'nicht-sensitiv' dokumentierten) Endpunkt an die GUI ausgeliefert wird
    -- analog zu redact_url() in docker/entrypoint.sh. HVNB_GIT_REPO_URL
    kann bei einem privaten Repository per HTTPS+Token konfiguriert sein
    (siehe DEPLOYMENT.md 4b), der Token darf hier nicht im Klartext
    landen. file://- und git@-URLs sowie HTTPS ohne eingebettete
    Zugangsdaten bleiben unveraendert."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", url)


@router.get("", response_model=PublicSettings)
def get_public_settings(user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> PublicSettings:
    settings = get_settings()
    data = {field: getattr(settings, field) for field in PublicSettings.model_fields}
    data["git_repo_url"] = _redact_git_url(data["git_repo_url"])
    return PublicSettings(**data)


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
        last_retention_cleanup_at=(
            scheduler_status.last_retention_cleanup_at.isoformat()
            if scheduler_status and scheduler_status.last_retention_cleanup_at
            else None
        ),
        last_file_restore_expiry_at=(
            scheduler_status.last_file_restore_expiry_at.isoformat()
            if scheduler_status and scheduler_status.last_file_restore_expiry_at
            else None
        ),
    )


@router.get("/version-history", response_model=list[CommitInfo])
def get_version_history(limit: int = 100, user=Depends(get_current_user)) -> list[CommitInfo]:
    """Liste der letzten Commits (neuester zuerst) fuer die Versionshistorie-
    Seite (Fusszeile-Link) -- zeigt, welche Aenderungen mit welchem Push
    aufs verfolgte Repository (HVNB_GIT_REPO_URL) ausgeliefert wurden.
    Nutzt die ASCII-Steuerzeichen \\x1f (Feldtrenner) / \\x1e
    (Datensatztrenner) statt z.B. '|', da Commit-Nachrichten in diesem
    Projekt selbst laengere Freitext-Absaetze mit Sonderzeichen enthalten
    koennen, die sonst mit einem sichtbaren Trennzeichen kollidieren
    wuerden."""
    field_sep = "\x1f"
    record_sep = "\x1e"
    try:
        result = subprocess.run(
            [
                "git", "-C", str(APP_DIR), "log", f"-n{limit}",
                f"--pretty=format:%H{field_sep}%h{field_sep}%ad{field_sep}%s{field_sep}%b{record_sep}",
                "--date=iso-strict",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []

    commits: list[CommitInfo] = []
    for record in result.stdout.split(record_sep):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(field_sep)
        if len(parts) < 4:
            continue
        body = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
        commits.append(CommitInfo(hash=parts[0], short_hash=parts[1], date=parts[2], subject=parts[3], body=body))
    return commits
