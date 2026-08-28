"""Periodischer Hintergrundabgleich: Cluster-Health-Check, volle Discovery
und Snapshot-Abgleich liefen bisher ausschliesslich manuell per Knopfdruck
in der GUI. Dieses Modul registriert die drei Jobs als APScheduler-Tasks
(siehe app.main.lifespan fuer Start/Stop) -- jeder Job oeffnet seine eigene
DB-Session (SessionLocal), analog zum Hintergrund-Restore-Lauf in
app.api.routes.restore._execute_restore, und behandelt jedes betroffene
Objekt (Cluster bzw. Snapshot-Gruppe) einzeln per try/except, damit ein
nicht erreichbarer Cluster nicht den Abgleich der anderen verhindert.

Ruft bewusst dieselben Kernfunktionen auf, die auch die manuellen
"Verify"/"Discover"-Buttons in der GUI nutzen (_refresh_status,
_run_discovery / _discover_and_persist), statt die Logik zu duplizieren."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.api.routes.file_restore import _cleanup_file_restore_run
from app.api.routes.hyperv_clusters import _refresh_status as _refresh_hyperv_status
from app.api.routes.hyperv_clusters import _run_discovery as _run_hyperv_discovery
from app.api.routes.jobs import trigger_job_run
from app.api.routes.netapp_clusters import _discover_and_persist as _run_netapp_discovery
from app.api.routes.netapp_clusters import _refresh_status as _refresh_netapp_status
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.backup_policy import BackupPolicy, RetentionType
from app.models.backup_run import BackupRun, BackupRunSnapshot
from app.models.file_restore_run import FileRestoreRun
from app.models.hyperv_cluster import HyperVCluster
from app.models.netapp_cluster import NetAppCluster
from app.models.schedule import Schedule, ScheduleType
from app.models.scheduler_status import SchedulerStatus

_scheduler: BackgroundScheduler | None = None


def _log(message: str) -> None:
    print(f"[scheduler] {datetime.now(timezone.utc).isoformat()} {message}", flush=True)


def _touch(db: Session, field_name: str) -> None:
    """Aktualisiert den Zeitstempel eines Jobs auf der Singleton-Statuszeile
    -- fuer die Fusszeile im Hauptmenue ('Letzte Discovery: ...'). Laeuft
    unabhaengig vom eigentlichen Job-Ergebnis, damit auch ein Lauf ohne
    Aenderungen (z.B. keine Snapshots zu pruefen) korrekt als 'gelaufen'
    gilt."""
    row = db.query(SchedulerStatus).first()
    if row is None:
        row = SchedulerStatus()
        db.add(row)
    setattr(row, field_name, datetime.now(timezone.utc))
    db.commit()


def run_health_checks() -> None:
    db = SessionLocal()
    try:
        for cluster in db.query(HyperVCluster).all():
            try:
                _refresh_hyperv_status(db, cluster)
            except Exception as exc:  # WinRM-Verbindungsfehler sind keine einheitliche Exception-Klasse
                _log(f"Health-Check fehlgeschlagen fuer Hyper-V-Cluster '{cluster.name}': {exc}")
        for cluster in db.query(NetAppCluster).all():
            try:
                _refresh_netapp_status(db, cluster)
            except Exception as exc:
                _log(f"Health-Check fehlgeschlagen fuer NetApp-Cluster '{cluster.name}': {exc}")
        _touch(db, "last_health_check_at")
    finally:
        db.close()


def run_discovery() -> None:
    db = SessionLocal()
    try:
        for cluster in db.query(HyperVCluster).all():
            try:
                _run_hyperv_discovery(db, cluster)
            except Exception as exc:
                _log(f"Discovery fehlgeschlagen fuer Hyper-V-Cluster '{cluster.name}': {exc}")
        for cluster in db.query(NetAppCluster).all():
            try:
                _run_netapp_discovery(db, cluster)
            except Exception as exc:
                _log(f"Discovery fehlgeschlagen fuer NetApp-Cluster '{cluster.name}': {exc}")
        _touch(db, "last_discovery_at")
    finally:
        db.close()


def run_snapshot_reconciliation() -> None:
    """Vergleicht als 'success' markierte BackupRunSnapshot-Zeilen mit den
    tatsaechlich auf dem jeweiligen NetApp-Volume vorhandenen Snapshots.
    Fehlt ein Snapshot (z.B. durch ONTAP-Retention oder manuelles Aufraeumen
    ausserhalb der App geloescht), wird die Zeile auf success=False gesetzt
    -- kein Hard-Delete, damit die Lauf-Historie erhalten bleibt, die Zeile
    verschwindet dadurch aber aus der Restore-Auswahl (siehe
    list_backups_for_object in app.api.routes.jobs, das nach success=True
    filtert)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(BackupRunSnapshot)
            .filter(BackupRunSnapshot.success.is_(True), BackupRunSnapshot.volume_uuid.isnot(None))
            .all()
        )
        if not rows:
            return

        groups: dict[tuple[str, str], list[BackupRunSnapshot]] = defaultdict(list)
        for row in rows:
            groups[(row.netapp_cluster_id, row.volume_uuid)].append(row)

        clusters = {c.id: c for c in db.query(NetAppCluster).all()}
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        for (cluster_id, volume_uuid), group_rows in groups.items():
            cluster = clusters.get(cluster_id)
            if cluster is None:
                continue
            try:
                service = _netapp_service_for(cluster)
                real_names = service.list_snapshot_names(volume_uuid)
            except Exception as exc:
                _log(f"Snapshot-Abgleich uebersprungen fuer Cluster '{cluster.name}'/Volume '{volume_uuid}': {exc}")
                continue

            for row in group_rows:
                if row.snapshot_name and row.snapshot_name not in real_names:
                    row.success = False
                    row.error_message = f"Snapshot wurde extern geloescht (Abgleich am {now_str} UTC)"
            db.commit()
    finally:
        _touch(db, "last_snapshot_reconciliation_at")
        db.close()


def run_retention_cleanup() -> None:
    """Setzt die in jeder Policy hinterlegte Retention (retention_type/
    retention_value) tatsaechlich durch -- bislang wurden diese Felder nur
    gespeichert, aber nie ausgewertet, sodass Snapshots unbegrenzt auf den
    NetApp-Volumes verblieben. Laeuft (wie run_snapshot_reconciliation)
    einmal taeglich statt direkt nach jedem Backup-Lauf: Retention ist auf
    Tage/Anzahl skaliert, also zeitlich unkritisch, und ein taeglicher Job
    faengt auch Snapshots ab, die zu einer inzwischen deaktivierten oder
    fehlgeschlagenen Policy gehoeren -- ein 'nach jedem Backup pruefen'
    wuerde das verpassen.

    Gruppiert alle erfolgreichen BackupRunSnapshot-Zeilen einer Policy nach
    (netapp_cluster_id, volume_uuid) -- mehrere Backup-Laeufe derselben
    Policy landen ueblicherweise auf demselben Volume, die Retention wird
    also pro Volume durchgesetzt, nicht global pro Policy. Bei COUNT werden
    die neuesten N behalten, bei DAYS alle juenger als N Tage. Ein
    fehlgeschlagenes Loeschen (z.B. Snapshot-Locking/SnapLock noch aktiv)
    wird geloggt und die Zeile bleibt stehen -- naechster Lauf versucht es
    erneut, sobald die Sperre ausgelaufen ist."""
    db = SessionLocal()
    try:
        clusters = {c.id: c for c in db.query(NetAppCluster).all()}
        now = datetime.now(timezone.utc)

        for policy in db.query(BackupPolicy).all():
            rows = (
                db.query(BackupRunSnapshot)
                .join(BackupRun, BackupRunSnapshot.run_id == BackupRun.id)
                .filter(
                    BackupRun.policy_id == policy.id,
                    BackupRunSnapshot.success.is_(True),
                    BackupRunSnapshot.volume_uuid.isnot(None),
                )
                .all()
            )
            if not rows:
                continue

            groups: dict[tuple[str, str], list[BackupRunSnapshot]] = defaultdict(list)
            for row in rows:
                groups[(row.netapp_cluster_id, row.volume_uuid)].append(row)

            for (cluster_id, volume_uuid), group_rows in groups.items():
                cluster = clusters.get(cluster_id)
                if cluster is None:
                    continue
                group_rows.sort(key=lambda r: r.created_at, reverse=True)

                if policy.retention_type == RetentionType.COUNT:
                    to_delete = group_rows[policy.retention_value:] if policy.retention_value > 0 else []
                else:  # RetentionType.DAYS
                    cutoff = now - timedelta(days=policy.retention_value)
                    to_delete = [r for r in group_rows if r.created_at < cutoff]

                if not to_delete:
                    continue

                try:
                    service = _netapp_service_for(cluster)
                except Exception as exc:
                    _log(f"Retention uebersprungen fuer Cluster '{cluster.name}': {exc}")
                    continue

                for row in to_delete:
                    try:
                        result = service.delete_snapshot(row.volume_uuid, row.snapshot_uuid)
                    except Exception as exc:
                        _log(f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}') konnte nicht geloescht werden: {exc}")
                        continue
                    if not result.success:
                        _log(f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}') konnte nicht geloescht werden: {result.message}")
                        continue
                    db.delete(row)
                    _log(
                        f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}', "
                        f"Retention {policy.retention_type.value}={policy.retention_value}) geloescht"
                    )
                db.commit()
    finally:
        _touch(db, "last_retention_cleanup_at")
        db.close()


def run_file_restore_expiry() -> None:
    """Sicherheitsnetz fuer Datei-Restore-Sessions (siehe
    app.api.routes.file_restore): raeumt gemountete VHDX automatisch auf,
    wenn der Nutzer den manuellen Cleanup vergessen hat. Laeuft stuendlich
    statt taeglich wie die Retention -- diese Sessions sollen kurzlebig
    sein (nur fuer den Dauer eines Datei-Restore-Vorgangs offen), ein
    24h-Sicherheitsnetz soll also zeitnah greifen, nicht erst am naechsten
    Tag. Nutzt denselben Cleanup-Ablauf wie der manuelle Endpunkt
    (_cleanup_file_restore_run), damit kein zweiter Code-Pfad fuers
    Abbauen von LUN-Klon/iSCSI/Mount gepflegt werden muss."""
    db = SessionLocal()
    try:
        settings = get_settings()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.file_restore_max_age_hours)
        expired = (
            db.query(FileRestoreRun)
            .filter(FileRestoreRun.cleanup_needed.is_(True), FileRestoreRun.started_at < cutoff)
            .all()
        )
        for run in expired:
            try:
                _cleanup_file_restore_run(db, run)
                run.error_message = "Automatisch aufgeraeumt (Zeitlimit ueberschritten)"
                db.commit()
                _log(f"Datei-Restore-Session fuer VM '{run.vm_name}' automatisch aufgeraeumt (Zeitlimit ueberschritten)")
            except Exception as exc:
                _log(f"Automatisches Aufraeumen der Datei-Restore-Session fuer VM '{run.vm_name}' fehlgeschlagen: {exc}")
    finally:
        _touch(db, "last_file_restore_expiry_at")
        db.close()


def _schedule_is_due(schedule: Schedule, now_local: datetime) -> bool:
    """Prueft, ob ein Schedule genau zur aktuellen (lokalen) Minute faellig
    ist. 'times' enthaelt "HH:MM"-Strings (bei HOURLY mehrere, sonst genau
    einen, siehe Schedule-Modell) -- ein reiner String-Vergleich reicht
    daher fuer alle vier Typen, WEEKLY/MONTHLY brauchen zusaetzlich den
    Wochentag/Tag-Abgleich."""
    if now_local.strftime("%H:%M") not in schedule.times:
        return False
    if schedule.schedule_type == ScheduleType.WEEKLY:
        return schedule.weekday is not None and now_local.weekday() == schedule.weekday
    if schedule.schedule_type == ScheduleType.MONTHLY:
        return schedule.day_of_month is not None and now_local.day == schedule.day_of_month
    return True


def run_scheduled_backups() -> None:
    """Fuehrt faellige Backup-Policies automatisch aus -- bislang gab es
    dafuer ueberhaupt keinen Trigger, Zeitplaene (Schedule) waren rein
    dekorativ und jeder Lauf musste manuell ueber 'Jetzt ausfuehren'
    angestossen werden. Laeuft minuetlich (siehe start_scheduler); die in
    Backup > Zeitplaene hinterlegten Uhrzeiten werden in der konfigurierten
    lokalen Zeitzone interpretiert (HVNB_SCHEDULE_TIMEZONE, Default
    Europe/Vienna), NICHT UTC -- der Container selbst laeuft komplett in
    UTC, ein Admin, der '08:30' eintraegt, meint aber die eigene Ortszeit.
    Ruft trigger_job_run() direkt auf (dieselbe Funktion, die der manuelle
    'Jetzt ausfuehren'-Button verwendet), statt die Ausfuehrungslogik zu
    duplizieren -- dieser Aufruf blockiert bis der komplette Lauf (inkl.
    etwaiger Checkpoints) fertig ist, was fuer den eigenen Thread des
    BackgroundScheduler unproblematisch ist. max_instances=1 (siehe
    start_scheduler) verhindert, dass ein noch laufender Lauf durch die
    naechste Minute ueberlappend erneut angestossen wird."""
    db = SessionLocal()
    try:
        settings = get_settings()
        try:
            tz = ZoneInfo(settings.schedule_timezone)
        except Exception:
            _log(f"Ungueltige HVNB_SCHEDULE_TIMEZONE '{settings.schedule_timezone}', falle auf UTC zurueck")
            tz = timezone.utc
        now_local = datetime.now(tz)

        policies = db.query(BackupPolicy).filter(BackupPolicy.enabled.is_(True), BackupPolicy.schedule_id.isnot(None)).all()
        for policy in policies:
            schedule = db.get(Schedule, policy.schedule_id)
            if schedule is None or not _schedule_is_due(schedule, now_local):
                continue
            try:
                trigger_job_run(policy.id, db=db, user=None)
                _log(f"Geplanter Backup-Lauf gestartet: Policy '{policy.name}' (Zeitplan '{schedule.name}')")
            except Exception as exc:
                _log(f"Geplanter Backup-Lauf fuer Policy '{policy.name}' fehlgeschlagen: {exc}")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_health_checks, IntervalTrigger(minutes=settings.healthcheck_interval_minutes),
        id="health-check", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_discovery, IntervalTrigger(minutes=settings.discovery_interval_minutes),
        id="discovery", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_snapshot_reconciliation, CronTrigger(hour=settings.snapshot_reconcile_hour, minute=0),
        id="snapshot-reconciliation", replace_existing=True, max_instances=1,
    )
    # 15 Minuten nach dem Snapshot-Abgleich, damit Retention nicht gegen
    # denselben Volume-Bestand parallel zum Abgleich arbeitet (nicht
    # zwingend noetig, aber vermeidet ueberlappende NetApp-API-Last).
    retention_hour = settings.snapshot_reconcile_hour
    scheduler.add_job(
        run_retention_cleanup, CronTrigger(hour=retention_hour, minute=15),
        id="retention-cleanup", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_scheduled_backups, CronTrigger(minute="*"),
        id="scheduled-backups", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_file_restore_expiry, IntervalTrigger(hours=1),
        id="file-restore-expiry", replace_existing=True, max_instances=1,
    )
    scheduler.start()
    _log(
        f"gestartet (Health-Check alle {settings.healthcheck_interval_minutes}min, "
        f"Discovery alle {settings.discovery_interval_minutes}min, "
        f"Snapshot-Abgleich taeglich um {settings.snapshot_reconcile_hour:02d}:00 UTC, "
        f"Retention-Cleanup taeglich um {retention_hour:02d}:15 UTC, "
        f"geplante Backups minuetlich geprueft in Zeitzone {settings.schedule_timezone}, "
        f"Datei-Restore-Sicherheitsnetz stuendlich (Zeitlimit {settings.file_restore_max_age_hours}h))"
    )
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
