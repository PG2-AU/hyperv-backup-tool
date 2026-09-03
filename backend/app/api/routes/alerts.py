"""Dashboard 'Warnungen' + neue Alarme-Seite: liefert eine EINE
zusammengefuehrte Liste aller aktuellen und historischen Warnungen --
persistierte Alert-Zeilen (Kapazitaet, Cluster-/SnapMirror-Gesundheit,
siehe app.core.scheduler.run_alert_check) gemischt mit live aus BackupRun
abgeleiteten Eintraegen fuer fehlgeschlagene Backup-Laeufe (bewusst nicht
dupliziert gespeichert, siehe app.models.alert)."""

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.netapp_clusters import _discover_and_persist
from app.core.rbac import Permission
from app.core.scheduler import run_alert_check
from app.db.session import get_db
from app.models.alert import Alert, AlertConfig, AlertScope, AlertStatus, AlertType
from app.models.allowed_schedule_collision import AllowedScheduleCollision
from app.models.backup_run import BackupRun, JobStatus
from app.models.netapp_cluster import NetAppCluster
from app.schemas.alert import AlertConfigRead, AlertConfigUpdate, AlertRead, AllowedScheduleCollisionRead

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
                resource_group_id=a.resource_group_id,
                policy_id=a.policy_id,
            )
        )

    # Fehlgeschlagene Backup-Laeufe: virtuell aus BackupRun abgeleitet
    # (siehe app.models.alert-Docstring) -- 'aufgeloest', sobald ein
    # SPAETERER Lauf derselben Policy erfolgreich war ODER manuell quittiert
    # wurde (BackupRun.alert_dismissed_at) -- ohne Quittieren-Option bliebe
    # der Alarm bei einer selten laufenden oder inzwischen deaktivierten
    # Policy sonst fuer immer aktiv (Nutzer-Meldung).
    runs_by_policy: dict[str, list[BackupRun]] = defaultdict(list)
    for run in db.query(BackupRun).order_by(BackupRun.started_at).all():
        if run.policy_id:
            runs_by_policy[run.policy_id].append(run)

    for policy_runs in runs_by_policy.values():
        for i, run in enumerate(policy_runs):
            if run.status not in (JobStatus.FAILED, JobStatus.CLEANED_UP_AFTER_FAILURE):
                continue
            resolved_run = next((r for r in policy_runs[i + 1 :] if r.status == JobStatus.SUCCEEDED), None)
            resolved_at = resolved_run.started_at if resolved_run else run.alert_dismissed_at
            results.append(
                AlertRead(
                    id=f"backup-{run.id}",
                    alert_type="backup_failed",
                    object_name=run.policy_name,
                    message=run.error_message or "Backup-Lauf fehlgeschlagen",
                    status="resolved" if resolved_at else "active",
                    triggered_at=run.started_at,
                    resolved_at=resolved_at,
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
    config.volume_threshold_percent = payload.volume_threshold_percent
    config.lun_threshold_percent = payload.lun_threshold_percent
    config.snapmirror_lag_threshold_hours = payload.snapmirror_lag_threshold_hours
    config.backup_missed_grace_minutes = payload.backup_missed_grace_minutes
    config.schedule_collision_window_minutes = payload.schedule_collision_window_minutes
    config.scope = AlertScope(payload.scope)
    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)

    # Sofort mit den neuen Schwellwerten/Scope neu bewerten, statt auf das
    # naechste 15min-Intervall zu warten -- sonst blieben z.B. beim
    # Umschalten auf den engeren Hyper-V-Scope bestehende, jetzt nicht mehr
    # zutreffende Alarme faelschlich als 'aktiv' stehen, bis der naechste
    # periodische Lauf sie aufloest.
    run_alert_check()

    return config


@router.post("/recheck", status_code=204)
def recheck_alerts(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> None:
    """Manueller Sofort-Check nach einer Aktion auf der Alarme-Seite (z.B.
    'Volume vergrößern' -> Resize durchgefuehrt) -- fuehrt zuerst eine volle
    NetApp-Discovery aus (damit percent_used/used_bytes aktuell sind, statt
    auf das naechste Discovery-Intervall zu warten), dann den Alert-Check
    selbst. Ein einzelner nicht erreichbarer Cluster verhindert nicht die
    Discovery der anderen (best-effort, analog zu run_discovery)."""
    for cluster in db.query(NetAppCluster).all():
        try:
            _discover_and_persist(db, cluster)
        except Exception:
            pass
    run_alert_check()


@router.post("/backup-runs/{run_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_backup_failed_alert(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> None:
    """Quittiert den virtuellen 'Backup fehlgeschlagen'-Alarm zu einem
    einzelnen Lauf (siehe list_alerts oben) -- fuer den Fall, dass die
    zugehoerige Policy selten laeuft, deaktiviert oder geloescht wurde und
    der Alarm sich sonst nie von selbst durch einen spaeteren erfolgreichen
    Lauf aufloesen wuerde."""
    run = db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup-Lauf nicht gefunden")
    run.alert_dismissed_at = datetime.now(timezone.utc)
    db.commit()


@router.post("/{alert_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_alert(
    alert_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> None:
    """Quittiert eine ECHTE, persistierte Alert-Zeile (Kapazitaet/Cluster-
    Gesundheit/SnapMirror/backup_missed) manuell -- anders als bei den
    virtuellen 'Backup fehlgeschlagen'-Alarmen (siehe dismiss_backup_failed_
    alert oben) gibt es hier ein echtes Alert.id. Fuer die meisten
    Alarm-Typen ohnehin nur temporaer wirksam, da run_alert_check einen
    weiterhin zutreffenden Zustand beim naechsten Durchlauf erneut meldet
    -- fuer backup_missed dagegen die einzige Moeglichkeit, einen Alarm
    loszuwerden, da ein verpasster Lauf sich nie von selbst 'aufloest'
    (siehe run_alert_check, das backup_missed bewusst von der
    automatischen Aufloesung ausnimmt)."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm nicht gefunden")
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    db.commit()


@router.post("/{alert_id}/allow-collision", status_code=status.HTTP_204_NO_CONTENT)
def allow_schedule_collision(
    alert_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> None:
    """Erlaubt eine Zeitplan-Kollision dauerhaft (Nutzer-Vorgabe: zwei
    Jobs sollen bewusst kollidierend laufen duerfen, ohne bei jedem
    15min-Check erneut zu melden) -- anders als der generische dismiss()
    oben, der bei einem sich selbst aufloesenden Alarmtyp wie
    SCHEDULE_COLLISION beim naechsten Durchlauf sofort wieder auftauchen
    wuerde, da die zugrundeliegende Konfiguration ja unveraendert bleibt.
    Legt dafuer eine AllowedScheduleCollision-Zeile mit demselben
    object_key an (siehe run_alert_check), die run_alert_check kuenftig
    vor dem Ausloesen prueft, und quittiert den aktuell aktiven Alarm
    sofort."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alarm nicht gefunden")
    if alert.alert_type != AlertType.SCHEDULE_COLLISION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nur fuer Zeitplan-Kollisionen verfuegbar")
    if not db.query(AllowedScheduleCollision).filter(AllowedScheduleCollision.collision_key == alert.object_key).first():
        db.add(AllowedScheduleCollision(collision_key=alert.object_key, summary=alert.message, allowed_at=datetime.now(timezone.utc)))
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    db.commit()


@router.get("/allowed-collisions", response_model=list[AllowedScheduleCollisionRead])
def list_allowed_collisions(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> list[AllowedScheduleCollision]:
    """Fuer die Verwaltungsliste in Settings > Alarme -- zeigt alle
    dauerhaft erlaubten Zeitplan-Kollisionen, mit der Moeglichkeit, eine
    Erlaubnis wieder zurueckzunehmen (DELETE unten)."""
    return db.query(AllowedScheduleCollision).order_by(AllowedScheduleCollision.allowed_at.desc()).all()


@router.delete("/allowed-collisions/{allowed_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_allowed_collision(
    allowed_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> None:
    """Nimmt eine zuvor erlaubte Kollision wieder zurueck -- besteht die
    zugrundeliegende Zeitplan-Ueberschneidung weiterhin, meldet sie der
    naechste Warnungs-Check erneut."""
    allowed = db.get(AllowedScheduleCollision, allowed_id)
    if allowed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Erlaubte Kollision nicht gefunden")
    db.delete(allowed)
    db.commit()
