"""Settings > DB-Sicherung (Backlog #66). Logik siehe app.core.db_backup."""

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.crypto import encrypt_secret
from app.core.db_backup import (
    INSTANCE_NAME_RE,
    BackupFileInfo,
    DbBackupError,
    fetch_backup,
    inspect_backup,
    instance_name,
    list_backups,
    restore_database,
    run_db_backup,
    running_jobs,
    test_target,
    unpack_to_temp,
)
from app.core.rbac import Permission
from app.core.scheduler import get_scheduler, run_scheduled_db_backup
from app.db.session import get_db
from app.models.db_backup import DbBackupConfig
from app.services.smb_target import SmbTargetError, split_unc

router = APIRouter(prefix="/api/db-backup", tags=["db-backup"])

_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CONFIRM_WORD = "WIEDERHERSTELLEN"


class DbBackupConfigRead(BaseModel):
    enabled: bool
    instance_name: str
    share_path: str
    username: str
    password_set: bool
    hour_utc: int
    retention_days: int
    local_keep: int
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_file_name: str | None = None
    last_size_bytes: int | None = None
    last_error: str | None = None
    last_upload_failed: bool = False


class DbBackupConfigWrite(BaseModel):
    enabled: bool
    instance_name: str = Field(default="hvnb", max_length=40)
    share_path: str = Field(max_length=1000)
    username: str = Field(max_length=255)
    # None = unveraendert lassen, "" = loeschen
    password: str | None = None
    hour_utc: int = Field(ge=0, le=23)
    retention_days: int = Field(ge=1, le=3650)
    local_keep: int = Field(ge=0, le=30)


class BackupFileRead(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime
    host: str
    pre_restore: bool


class BackupListRead(BaseModel):
    share: list[BackupFileRead]
    local: list[BackupFileRead]
    share_error: str | None = None


class RestoreSelection(BaseModel):
    source: str  # 'share' | 'local'
    name: str


class RestoreRequest(RestoreSelection):
    confirm: str


class RestorePreview(BaseModel):
    counts: dict[str, int]
    latest_backup_run_at: str | None = None
    secret_key_ok: bool | None = None
    blockers: list[str]
    running_jobs: list[str]
    confirm_word: str = _CONFIRM_WORD


class RestoreResult(BaseModel):
    safety_copy: str
    message: str


def _get_or_create(db: Session) -> DbBackupConfig:
    config = db.query(DbBackupConfig).first()
    if config is None:
        config = DbBackupConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _read(config: DbBackupConfig) -> DbBackupConfigRead:
    return DbBackupConfigRead(
        enabled=config.enabled, instance_name=instance_name(config), share_path=config.share_path, username=config.username,
        password_set=bool(config.encrypted_password), hour_utc=config.hour_utc, retention_days=config.retention_days,
        local_keep=config.local_keep, last_attempt_at=config.last_attempt_at, last_success_at=config.last_success_at,
        last_file_name=config.last_file_name, last_size_bytes=config.last_size_bytes, last_error=config.last_error,
        last_upload_failed=config.last_upload_failed,
    )


def _files(items: list[BackupFileInfo]) -> list[BackupFileRead]:
    return [BackupFileRead(**vars(i)) for i in items]


def reschedule_db_backup(hour_utc: int) -> None:
    scheduler = get_scheduler()
    if scheduler is None:
        return
    trigger = CronTrigger(hour=hour_utc, minute=30)
    if scheduler.get_job("db-backup"):
        scheduler.reschedule_job("db-backup", trigger=trigger)
    else:
        scheduler.add_job(run_scheduled_db_backup, trigger, id="db-backup", replace_existing=True, max_instances=1)


@router.get("", response_model=DbBackupConfigRead)
def get_config(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> DbBackupConfigRead:
    return _read(_get_or_create(db))


@router.put("", response_model=DbBackupConfigRead)
def update_config(
    payload: DbBackupConfigWrite, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> DbBackupConfigRead:
    share_path = payload.share_path.strip()
    if payload.enabled or share_path:
        try:
            split_unc(share_path)
        except SmbTargetError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    name = payload.instance_name.strip()
    if not INSTANCE_NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kennung: 1-40 Zeichen, nur Buchstaben, Ziffern und Bindestrich (z. B. prod oder svaudemo7).",
        )
    config = _get_or_create(db)
    config.instance_name = name
    if payload.enabled and not config.enabled:
        config.enabled_since = datetime.now(timezone.utc)
    config.enabled = payload.enabled
    config.share_path = share_path
    config.username = payload.username.strip()
    if payload.password is not None:
        config.encrypted_password = encrypt_secret(payload.password) if payload.password else None
    config.hour_utc = payload.hour_utc
    config.retention_days = payload.retention_days
    config.local_keep = payload.local_keep
    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)
    reschedule_db_backup(config.hour_utc)
    return _read(config)


@router.post("/test")
def test_connection(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> dict:
    config = _get_or_create(db)
    try:
        return {"message": test_target(config)}
    except (SmbTargetError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/run", response_model=DbBackupConfigRead)
def run_now(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> DbBackupConfigRead:
    try:
        config = run_db_backup(db, triggered_by=f"manuell durch {user.display_name or user.username}")
    except DbBackupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _read(config)


@router.get("/backups", response_model=BackupListRead)
def get_backups(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> BackupListRead:
    listing = list_backups(db)
    return BackupListRead(share=_files(listing.share), local=_files(listing.local), share_error=listing.share_error)


def _preview(db: Session, path) -> RestorePreview:
    inspection = inspect_backup(path)
    return RestorePreview(
        counts=inspection.counts, latest_backup_run_at=inspection.latest_backup_run_at,
        secret_key_ok=inspection.secret_key_ok, blockers=inspection.blockers, running_jobs=running_jobs(db),
    )


@router.post("/restore/preview", response_model=RestorePreview)
def preview_restore(
    payload: RestoreSelection, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> RestorePreview:
    try:
        path = fetch_backup(db, payload.source, payload.name)
    except (DbBackupError, SmbTargetError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        return _preview(db, path)
    finally:
        path.unlink(missing_ok=True)


@router.post("/restore", response_model=RestoreResult)
def restore(
    payload: RestoreRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> RestoreResult:
    if payload.confirm.strip() != _CONFIRM_WORD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Zur Bestätigung '{_CONFIRM_WORD}' eingeben.")
    try:
        path = fetch_backup(db, payload.source, payload.name)
    except (DbBackupError, SmbTargetError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _do_restore(db, path, user, f"{'Freigabe' if payload.source == 'share' else 'lokaler Kopie'} {payload.name}")


@router.post("/restore/upload/preview", response_model=RestorePreview)
async def preview_upload(
    file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> RestorePreview:
    path = await _unpack_upload(file)
    try:
        return _preview(db, path)
    finally:
        path.unlink(missing_ok=True)


@router.post("/restore/upload", response_model=RestoreResult)
async def restore_upload(
    confirm: str, file: UploadFile = File(...), db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> RestoreResult:
    if confirm.strip() != _CONFIRM_WORD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Zur Bestätigung '{_CONFIRM_WORD}' eingeben.")
    path = await _unpack_upload(file)
    return _do_restore(db, path, user, f"hochgeladener Datei {file.filename}")


async def _unpack_upload(file: UploadFile):
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Datei ist zu groß.")
    try:
        return unpack_to_temp(data)
    except DbBackupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _do_restore(db: Session, path, user, source_label: str) -> RestoreResult:
    inspection = inspect_backup(path)
    if inspection.blockers:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(inspection.blockers))
    try:
        safety = restore_database(db, path, actor=user.display_name or user.username, source_label=source_label)
    except DbBackupError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RestoreResult(
        safety_copy=safety,
        message="Datenbank wiederhergestellt -- die Anwendung startet jetzt neu, danach bitte neu anmelden.",
    )
