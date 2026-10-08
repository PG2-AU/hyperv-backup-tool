"""Settings > System: Konfiguration exportieren/importieren (Backlog #45).
Logik und Designentscheidungen siehe app.core.config_transfer.

Export wird direkt beim Aufruf gebaut und als Download geschickt -- keine
Kopie bleibt auf dem Server liegen. Import ist zustandslos: die Vorschau
(POST /import/preview) und der eigentliche Import (POST /import) bekommen
dieselbe Datei jeweils erneut hochgeladen."""

import socket
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.release import current_commit
from app.api.routes.winrm_certs import build_bundle
from app.core.config_transfer import ConfigTransferError, apply_import, build_export, installation_blockers, plan_import
from app.core.rbac import Permission
from app.core.scheduler import DISCOVERY_INTERVAL_ANCHOR, INTERVAL_ANCHOR, get_scheduler
from app.db.session import get_db
from app.models.alert import AlertConfig
from app.models.scheduler_config import SchedulerConfig
from app.models.system_log import SystemLogEvent
from app.models.winrm_cert import WinrmHostCertificate

router = APIRouter(prefix="/api/config-transfer", tags=["config-transfer"])

# Obergrenze fuer den Upload -- eine reine Konfiguration ist wenige hundert
# KB gross, mit Backup-Katalog je nach Historie einige MB.
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class ImportTableCount(BaseModel):
    table: str
    label: str
    count: int


class ImportPreview(BaseModel):
    created_at: str | None = None
    created_by: str | None = None
    app_commit: str | None = None
    include_catalog: bool = False
    tables: list[ImportTableCount]
    manual_steps: list[str]
    warnings: list[str]
    # Nicht leer = Import nicht moeglich (Grund steht drin).
    blockers: list[str]


class ImportResultRead(BaseModel):
    imported: dict[str, int]
    paused_groups: list[str]
    skipped_users: list[str]
    manual_steps: list[str]
    bundle_rebuilt: bool


class ImportStatus(BaseModel):
    # Leer = Import moeglich, sonst was die Installation "nicht leer" macht.
    blockers: list[str]


def _actor(user) -> str:
    return user.display_name or user.username


def _log(db: Session, message: str) -> None:
    db.add(SystemLogEvent(level="INFO", source="config-transfer", message=message))
    db.commit()


def _app_commit() -> str | None:
    commit, _ = current_commit()
    return commit[:7] if commit else None


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Datei ist zu groß.")
    return data


@router.get("/export")
def export_config(
    include_catalog: bool = False, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> Response:
    data, manifest = build_export(db, include_catalog=include_catalog, exported_by=_actor(user), app_commit=_app_commit())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    host = socket.gethostname().split(".")[0] or "hvnb"
    filename = f"hvnb-config-{host}-{stamp}{'-mit-katalog' if include_catalog else ''}.zip"
    _log(
        db,
        f"Konfiguration exportiert{' inkl. Backup-Katalog' if include_catalog else ''} "
        f"({sum(manifest['counts'].values())} Einträge, durch {_actor(user)})",
    )
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/import/status", response_model=ImportStatus)
def import_status(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> ImportStatus:
    return ImportStatus(blockers=installation_blockers(db))


@router.post("/import/preview", response_model=ImportPreview)
async def preview_import(
    file: UploadFile = File(...), db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> ImportPreview:
    try:
        plan = plan_import(db, await _read_upload(file))
    except ConfigTransferError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    m = plan.manifest
    return ImportPreview(
        created_at=m.get("created_at"), created_by=m.get("created_by"), app_commit=m.get("app_commit"),
        include_catalog=bool(m.get("include_catalog")),
        tables=[ImportTableCount(**t) for t in plan.summary()],
        manual_steps=list(m.get("manual_steps") or []), warnings=plan.warnings, blockers=plan.blockers,
    )


@router.post("/import", response_model=ImportResultRead)
async def run_import(
    file: UploadFile = File(...), db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> ImportResultRead:
    try:
        plan = plan_import(db, await _read_upload(file))
        result = apply_import(db, plan)
    except ConfigTransferError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # WinRM-Zertifikatsbundle aus den importierten Zertifikaten neu schreiben
    # (die Datei selbst liegt ausserhalb der DB und kommt nicht mit).
    bundle_rebuilt = False
    if db.query(WinrmHostCertificate).count():
        try:
            build_bundle(db, user)
            bundle_rebuilt = True
        except HTTPException:
            bundle_rebuilt = False

    _reschedule_background_jobs(db)
    _log(
        db,
        f"Konfiguration importiert (Export vom {plan.manifest.get('created_at')} durch {plan.manifest.get('created_by')}, "
        f"{sum(result.imported.values())} Einträge, {len(result.paused_groups)} Protection Groups pausiert, "
        f"durch {_actor(user)})",
    )
    return ImportResultRead(
        imported=result.imported, paused_groups=result.paused_groups, skipped_users=result.skipped_users,
        manual_steps=list(plan.manifest.get("manual_steps") or []), bundle_rebuilt=bundle_rebuilt,
    )


def _reschedule_background_jobs(db: Session) -> None:
    """Importierte Intervalle sofort wirksam machen -- gleiche Aufrufe wie
    update_scheduler_config/update_alert_config, ohne Container-Neustart."""
    scheduler = get_scheduler()
    if scheduler is None:
        return
    config = db.query(SchedulerConfig).first()
    if config is not None:
        scheduler.reschedule_job(
            "health-check", trigger=IntervalTrigger(minutes=config.healthcheck_interval_minutes, start_date=INTERVAL_ANCHOR)
        )
        scheduler.reschedule_job(
            "discovery", trigger=IntervalTrigger(minutes=config.discovery_interval_minutes, start_date=DISCOVERY_INTERVAL_ANCHOR)
        )
        scheduler.reschedule_job("snapshot-reconciliation", trigger=CronTrigger(hour=config.snapshot_reconcile_hour, minute=0))
        scheduler.reschedule_job("retention-cleanup", trigger=CronTrigger(hour=config.retention_cleanup_hour, minute=15))
        scheduler.reschedule_job(
            "vm-performance", trigger=IntervalTrigger(minutes=max(config.vm_perf_interval_minutes or 0, 1), start_date=INTERVAL_ANCHOR)
        )
    alert_config = db.query(AlertConfig).first()
    if alert_config is not None:
        scheduler.reschedule_job(
            "alert-check", trigger=IntervalTrigger(minutes=alert_config.alert_check_interval_minutes, start_date=INTERVAL_ANCHOR)
        )
