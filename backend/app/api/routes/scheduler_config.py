"""Settings > Hintergrundjobs: Zeitplaene der periodischen Hintergrundjobs
(Health-Check, Discovery, Snapshot-Abgleich, Retention-Cleanup) GUI-
konfigurierbar machen -- vorher nur per ENV/.env moeglich (siehe
app.core.config), Aenderungen brauchten dafuer immer einen Container-
Neustart. Eine Aktualisierung hier ruft scheduler.reschedule_job() live
auf der laufenden APScheduler-Instanz auf, siehe app.core.scheduler."""

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.core.scheduler import get_scheduler
from app.db.session import get_db
from app.models.scheduler_config import SchedulerConfig
from app.schemas.scheduler_config import SchedulerConfigRead, SchedulerConfigUpdate

router = APIRouter(prefix="/api/scheduler-config", tags=["scheduler-config"])


def _get_or_create(db: Session) -> SchedulerConfig:
    config = db.query(SchedulerConfig).first()
    if config is None:
        config = SchedulerConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.get("", response_model=SchedulerConfigRead)
def get_scheduler_config(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> SchedulerConfig:
    return _get_or_create(db)


@router.put("", response_model=SchedulerConfigRead)
def update_scheduler_config(
    payload: SchedulerConfigUpdate, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> SchedulerConfig:
    config = _get_or_create(db)
    config.healthcheck_interval_minutes = payload.healthcheck_interval_minutes
    config.discovery_interval_minutes = payload.discovery_interval_minutes
    config.snapshot_reconcile_hour = payload.snapshot_reconcile_hour
    config.retention_cleanup_hour = payload.retention_cleanup_hour
    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)

    # Live umschalten, ohne Container-Neustart -- reschedule_job() ersetzt
    # den bestehenden Trigger derselben Job-ID, die naechste Ausfuehrung
    # wird sofort nach dem neuen Trigger berechnet.
    scheduler = get_scheduler()
    if scheduler is not None:
        scheduler.reschedule_job("health-check", trigger=IntervalTrigger(minutes=config.healthcheck_interval_minutes))
        scheduler.reschedule_job("discovery", trigger=IntervalTrigger(minutes=config.discovery_interval_minutes))
        scheduler.reschedule_job("snapshot-reconciliation", trigger=CronTrigger(hour=config.snapshot_reconcile_hour, minute=0))
        scheduler.reschedule_job("retention-cleanup", trigger=CronTrigger(hour=config.retention_cleanup_hour, minute=15))

    return config
