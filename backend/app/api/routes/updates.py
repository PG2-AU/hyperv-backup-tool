"""Settings > Updates: Release-Paket hochladen und einspielen lassen. Ablauf
und Dateien siehe app.core.app_update; eingespielt wird vom Host-Dienst
(scripts/hvnb-update), nicht von der App. Recht settings:manage."""

import hashlib
import re
from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core import app_update
from app.core.rbac import Permission
from app.core.release import release_info
from app.db.session import get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.system_log import SystemLogEvent

router = APIRouter(prefix="/api/updates", tags=["updates"])

_manage = require_permission(Permission.SETTINGS_MANAGE)


class StagedPackage(BaseModel):
    package: str
    version: str
    sha256: str
    size_bytes: int
    uploaded_by: str | None = None
    uploaded_at: str | None = None


class UpdateResult(BaseModel):
    status: str  # running | succeeded | failed
    package: str | None = None
    version: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    log: str | None = None


class AutoUpdate(BaseModel):
    """Auto-Update aus Git (nur Entwicklungsumgebungen, auf dem Server eingerichtet)."""

    enabled: bool
    branch: str | None = None
    repo: str | None = None
    interval_minutes: str | None = None
    # idle | current | available | building | installing | waiting | failed | error
    state: str | None = None
    message: str | None = None
    target_commit: str | None = None
    installed_commit: str | None = None
    last_check_at: str | None = None
    last_update_at: str | None = None


class RegistryState(BaseModel):
    """Online-Update aus einer Registry (auf dem Server hinterlegt)."""

    repo: str
    latest_version: str | None = None
    checked_at: str | None = None
    error: str | None = None
    # automatisch einspielen, sobald eine neuere Version veroeffentlicht ist
    auto_enabled: bool = False


class GitConfigWrite(BaseModel):
    """Auto-Update aus Git einrichten bzw. aendern. Die Adresse darf Zugangsdaten
    enthalten (https://benutzer:token@...); sie wird nie zurueckgegeben."""

    repo: str = Field(min_length=3, max_length=500)
    branch: str = Field(default="master", min_length=1, max_length=200)
    interval_minutes: int = Field(default=5, ge=1, le=1440)


class GitConfigResult(BaseModel):
    action: str | None = None
    status: str | None = None  # ok | failed
    message: str | None = None
    at: str | None = None


class RegistryInstall(BaseModel):
    version: str


class AutoUpdateWrite(BaseModel):
    enabled: bool


class UpdateStatus(BaseModel):
    # release = Release-Image (Upload moeglich), git = bisherige Auslieferung
    delivery: str
    version: str | None = None
    agent_active: bool = False
    agent_last_seen_at: str | None = None
    staged: StagedPackage | None = None
    # requested | running | None
    pending: str | None = None
    # package | registry-install | registry-check | git-check | git-install | git-configure | git-remove
    pending_action: str | None = None
    last_result: UpdateResult | None = None
    # None = auf dem Server nicht eingerichtet
    auto_update: AutoUpdate | None = None
    # None = keine Registry hinterlegt
    registry: RegistryState | None = None
    # Der Server kann ein Auto-Update aus Git einrichten (Skript + git vorhanden)
    git_available: bool = False
    git_config_result: GitConfigResult | None = None


def _user_name(user) -> str:
    return user.display_name or user.username


def _log(db: Session, message: str) -> None:
    db.add(SystemLogEvent(level="INFO", source="updates", message=message))
    db.commit()


def _status() -> UpdateStatus:
    release = release_info()
    agent = app_update.agent_state()
    staged = app_update.staged()
    result = app_update.last_result()
    auto = app_update.auto_update_state()
    registry = app_update.registry_state()
    git_result = app_update.git_config_result()
    return UpdateStatus(
        delivery="release" if release else "git",
        version=str(release.get("version")) if release and release.get("version") else None,
        agent_active=agent["active"], agent_last_seen_at=agent["last_seen_at"],
        staged=StagedPackage(**{k: staged.get(k) for k in StagedPackage.model_fields}) if staged else None,
        pending=app_update.pending(),
        pending_action=app_update.pending_action() if app_update.pending() else None,
        last_result=UpdateResult(**{k: result.get(k) for k in UpdateResult.model_fields}) if result else None,
        auto_update=AutoUpdate(**{k: (auto.get(k) or None) if k != "enabled" else auto["enabled"] for k in AutoUpdate.model_fields}) if auto else None,
        registry=RegistryState(**registry) if registry else None,
        git_available=agent["git_autoupdate"],
        git_config_result=GitConfigResult(**{k: git_result.get(k) for k in GitConfigResult.model_fields}) if git_result else None,
    )


def _require_release() -> None:
    if release_info() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Installation läuft noch nicht als Release-Image; ein Paket-Upload ist hier nicht möglich.",
        )


def _require_agent() -> None:
    if not app_update.agent_state()["active"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Der Update-Dienst auf dem Server meldet sich nicht. Einmalig einrichten mit 'hvnb-update --install-agent' "
                   "(siehe Installationsdokumentation).",
        )


def _require_no_backups(db: Session) -> None:
    busy = db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.RUNNING, JobStatus.PENDING, JobStatus.CLEANING_UP])).count()
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Es laufen noch {busy} Backup(s). Bitte abwarten.")


def _require_registry() -> dict:
    registry = app_update.registry_state()
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Auf dem Server ist keine Registry hinterlegt (hvnb-update --set-registry <Image-Pfad>).",
        )
    return registry


def _require_idle() -> None:
    if app_update.pending():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Es wird bereits ein Update eingespielt.")


@router.get("/status", response_model=UpdateStatus)
def get_status(user=Depends(_manage)) -> UpdateStatus:
    return _status()


@router.put("/package", response_model=UpdateStatus)
async def upload_package(
    request: Request, filename: str, sha256: str, db: Session = Depends(get_db), user=Depends(_manage),
) -> UpdateStatus:
    """Nimmt die Paketdatei als rohen Body entgegen (application/octet-stream)
    und schreibt sie direkt in den Uebergabe-Ordner -- kein Zwischenpuffern im
    Speicher. Die Pruefsumme wird beim Schreiben mitgerechnet."""
    _require_release()
    _require_idle()
    match = app_update.PACKAGE_RE.match(filename)
    if not match:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Erwartet wird eine Datei hvnb-<Version>.tar.gz.")
    expected = sha256.strip().lower()
    if not app_update.SHA256_RE.match(expected):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die Prüfsumme ist keine gültige SHA-256-Prüfsumme.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > app_update.MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Die Datei ist zu groß.")

    app_update.discard_staged()
    folder = app_update.inbox()
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f"{filename}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with open(part, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > app_update.MAX_PACKAGE_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Die Datei ist zu groß.")
                digest.update(chunk)
                await anyio.to_thread.run_sync(fh.write, chunk)
        if size == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die Datei ist leer.")
        if digest.hexdigest() != expected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Die Prüfsumme stimmt nicht mit der hochgeladenen Datei überein (beschädigt oder falsche Prüfsummendatei).",
            )
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(folder / filename)
    app_update.write_json("staged.json", {
        "package": filename, "version": match.group(1), "sha256": expected, "size_bytes": size,
        "uploaded_by": _user_name(user), "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _log(db, f"Update-Paket {filename} hochgeladen ({size // (1024 * 1024)} MB, Prüfsumme in Ordnung, durch {_user_name(user)})")
    return _status()


@router.delete("/package", response_model=UpdateStatus)
def discard_package(db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    _require_idle()
    staged = app_update.staged()
    app_update.discard_staged()
    if staged:
        _log(db, f"Update-Paket {staged['package']} verworfen (durch {_user_name(user)})")
    return _status()


@router.post("/install", response_model=UpdateStatus)
def install_package(db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Erteilt dem Host-Dienst den Auftrag, das hochgeladene Paket einzuspielen."""
    _require_release()
    _require_idle()
    staged = app_update.staged()
    if staged is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Es ist kein Paket hochgeladen.")
    _require_agent()
    _require_no_backups(db)
    app_update.write_json("request.json", {
        "package": staged["package"], "sha256": staged["sha256"], "version": staged["version"],
        "requested_by": _user_name(user), "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _log(db, f"Update auf Version {staged['version']} angefordert ({staged['package']}, durch {_user_name(user)})")
    return _status()


@router.put("/auto-update", response_model=UpdateStatus)
def set_auto_update(payload: AutoUpdateWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Schalter "automatisch einspielen" fuer das Update aus Git. Aus
    (Standard): der Server prueft und baut nur auf Knopfdruck. Ein: der Timer
    auf dem Server spielt jeden neuen Commit von selbst ein."""
    _require_release()
    app_update.set_auto_update(payload.enabled)
    _log(db, f"Automatische Updates aus Git {'eingeschaltet' if payload.enabled else 'ausgeschaltet'} (durch {_user_name(user)})")
    return _status()


@router.post("/registry/check", response_model=UpdateStatus)
def check_registry(user=Depends(_manage)) -> UpdateStatus:
    """Laesst den Server nachsehen, welche Version in der Registry die neueste
    ist. Das Ergebnis erscheint kurz darauf im Status (registry.checked_at)."""
    _require_release()
    _require_idle()
    _require_registry()
    _require_agent()
    app_update.write_json("request.json", {
        "action": "registry-check", "requested_by": _user_name(user),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return _status()


@router.post("/registry/install", response_model=UpdateStatus)
def install_from_registry(payload: RegistryInstall, db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Erteilt dem Server den Auftrag, eine Version aus der Registry zu holen und einzuspielen."""
    _require_release()
    _require_idle()
    registry = _require_registry()
    _require_agent()
    version = payload.version.strip()
    if not app_update.PACKAGE_RE.match(f"hvnb-{version}.tar.gz"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültige Versionsangabe.")
    _require_no_backups(db)
    app_update.write_json("request.json", {
        "action": "registry-install", "version": version, "package": f"{registry['repo']}:{version}",
        "requested_by": _user_name(user), "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _log(db, f"Online-Update auf Version {version} angefordert ({registry['repo']}, durch {_user_name(user)})")
    return _status()


@router.put("/registry/auto", response_model=UpdateStatus)
def set_registry_auto(payload: AutoUpdateWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Automatisches Online-Update ein/aus: eingeschaltet prueft der Server
    stuendlich und spielt eine neuere Version von selbst ein."""
    _require_release()
    _require_registry()
    app_update.update_settings(registry_auto_update="on" if payload.enabled else "off")
    _log(db, f"Automatische Online-Updates {'eingeschaltet' if payload.enabled else 'ausgeschaltet'} (durch {_user_name(user)})")
    return _status()


def _require_git_available() -> None:
    if not app_update.agent_state()["git_autoupdate"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Auf dem Server fehlt hvnb-git-autoupdate oder git. Aus einem aktuellen Paket 'hvnb-update --install-agent' "
                   "erneut ausführen und git installieren.",
        )


@router.put("/git-config", response_model=UpdateStatus)
def set_git_config(payload: GitConfigWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Auto-Update aus Git einrichten oder aendern: der Update-Dienst des Servers
    prueft die Adresse und richtet den Timer ein (Ergebnis in git_config_result).
    Nur fuer Entwicklungsumgebungen -- jeder Commit auf dem Branch wird gebaut
    und eingespielt."""
    _require_release()
    _require_idle()
    _require_agent()
    _require_git_available()
    repo, branch = payload.repo.strip(), payload.branch.strip()
    if not app_update.GIT_REPO_RE.match(repo):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die Git-Adresse enthält unzulässige Zeichen.")
    if not app_update.GIT_BRANCH_RE.match(branch):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Der Branch-Name enthält unzulässige Zeichen.")
    app_update.write_json("request.json", {
        "action": "git-configure", "repo": repo, "branch": branch, "interval_minutes": str(payload.interval_minutes),
        "requested_by": _user_name(user), "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    shown = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", repo)
    _log(db, f"Auto-Update aus Git eingerichtet: {shown} ({branch}), alle {payload.interval_minutes} min (durch {_user_name(user)})")
    return _status()


@router.delete("/git-config", response_model=UpdateStatus)
def remove_git_config(db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Auto-Update aus Git auf dem Server wieder entfernen."""
    _require_release()
    _require_idle()
    _require_agent()
    app_update.write_json("request.json", {
        "action": "git-remove", "requested_by": _user_name(user),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _log(db, f"Auto-Update aus Git entfernt (durch {_user_name(user)})")
    return _status()


def _require_git_configured() -> None:
    if app_update.auto_update_state() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Das Update aus Git ist auf dem Server nicht eingerichtet.",
        )


@router.post("/git/check", response_model=UpdateStatus)
def check_git(user=Depends(_manage)) -> UpdateStatus:
    """Laesst den Server nachsehen, ob es auf dem Branch einen neuen Commit gibt.
    Das Ergebnis erscheint kurz darauf im Status (auto_update.state/last_check_at)."""
    _require_release()
    _require_idle()
    _require_git_configured()
    _require_agent()
    app_update.write_json("request.json", {
        "action": "git-check", "requested_by": _user_name(user),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return _status()


@router.post("/git/install", response_model=UpdateStatus)
def install_from_git(db: Session = Depends(get_db), user=Depends(_manage)) -> UpdateStatus:
    """Erteilt dem Server den Auftrag, den neuesten Commit zu bauen und einzuspielen."""
    _require_release()
    _require_idle()
    _require_git_configured()
    _require_agent()
    _require_no_backups(db)
    app_update.write_json("request.json", {
        "action": "git-install", "requested_by": _user_name(user),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _log(db, f"Update aus Git angefordert (durch {_user_name(user)})")
    return _status()
