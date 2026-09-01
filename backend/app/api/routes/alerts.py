"""Dashboard 'Warnungen' + neue Alarme-Seite: liefert eine EINE
zusammengefuehrte Liste aller aktuellen und historischen Warnungen --
persistierte Alert-Zeilen (Kapazitaet, Cluster-/SnapMirror-Gesundheit,
siehe app.core.scheduler.run_alert_check) gemischt mit live aus BackupRun
abgeleiteten Eintraegen fuer fehlgeschlagene Backup-Laeufe (bewusst nicht
dupliziert gespeichert, siehe app.models.alert)."""

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.alert import Alert, AlertConfig, AlertType
from app.models.backup_run import BackupRun, JobStatus
from app.schemas.alert import AlertConfigRead, AlertConfigUpdate, AlertRead

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_CAPACITY_TYPES = {AlertType.CAPACITY_VOLUME, AlertType.CAPACITY_LUN}


@router.get("", response_model=list[AlertRead])
def list_alerts(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[AlertRead]:
    results: list[AlertRead] = []

    for a in db.query(Alert).all():
        results.append(
            AlertRead(
                id=a.id,
                alert_type=a.alert_type.value,
                object_name=a.object_name,
                netapp_cluster_id=a.netapp_cluster_id,
                netapp_cluster_name=a.netapp_cluster_name,
                hyperv_cluster_id=a.hyperv_cluster_id,
                svm_name=a.svm_name,
                message=a.message,
                threshold_percent=a.threshold_percent,
                triggered_percent=a.triggered_percent,
                status=a.status.value,
                triggered_at=a.triggered_at,
                resolved_at=a.resolved_at,
                object_uuid=a.object_key if a.alert_type in _CAPACITY_TYPES else None,
            )
        )

    # Fehlgeschlagene Backup-Laeufe: virtuell aus BackupRun abgeleitet
    # (siehe app.models.alert-Docstring) -- 'aufgeloest', sobald ein
    # SPAETERER Lauf derselben Policy erfolgreich war.
    runs_by_policy: dict[str, list[BackupRun]] = defaultdict(list)
    for run in db.query(BackupRun).order_by(BackupRun.started_at).all():
        if run.policy_id:
            runs_by_policy[run.policy_id].append(run)

    for policy_runs in runs_by_policy.values():
        for i, run in enumerate(policy_runs):
            if run.status not in (JobStatus.FAILED, JobStatus.CLEANED_UP_AFTER_FAILURE):
                continue
            resolved_run = next((r for r in policy_runs[i + 1 :] if r.status == JobStatus.SUCCEEDED), None)
            results.append(
                AlertRead(
                    id=f"backup-{run.id}",
                    alert_type="backup_failed",
                    object_name=run.policy_name,
                    message=run.error_message or "Backup-Lauf fehlgeschlagen",
                    status="resolved" if resolved_run else "active",
                    triggered_at=run.started_at,
                    resolved_at=resolved_run.started_at if resolved_run else None,
                    run_id=run.id,
                )
            )

    results.sort(key=lambda r: r.triggered_at, reverse=True)
    return results


@router.get("/config", response_model=AlertConfigRead)
def get_alert_config(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> AlertConfig:
    config = db.query(AlertConfig).first()
    if config is None:
        config = AlertConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.put("/config", response_model=AlertConfigRead)
def update_alert_config(
    payload: AlertConfigUpdate, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> AlertConfig:
    config = db.query(AlertConfig).first()
    if config is None:
        config = AlertConfig()
        db.add(config)
    config.capacity_threshold_percent = payload.capacity_threshold_percent
    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)
    return config
