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
from app.api.routes.jobs import _execute_job_run, _start_job_run
from app.api.routes.netapp_clusters import _discover_and_persist as _run_netapp_discovery
from app.api.routes.netapp_clusters import _refresh_status as _refresh_netapp_status
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.backup_policy import BackupPolicy, RetentionType
from app.models.backup_run import BackupRun, BackupRunSnapshot, BackupRunSnapshotDestination, JobStatus
from app.models.email_config import EmailConfig
from app.models.file_restore_run import FileRestoreRun
from app.models.hyperv_cluster import HyperVCluster
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSnapMirrorRelationship, NetAppVolume
from app.models.restore_run import RestoreRun, RestoreStatus
from app.models.schedule import Schedule, ScheduleType
from app.models.scheduler_status import SchedulerStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_recreate_run import VmRecreateRun
from app.services.email_service import DailySummaryFailure, DailySummaryRow, DailySummaryStats, send_daily_summary

_scheduler: BackgroundScheduler | None = None


def _log(db: Session | None, message: str, level: str = "INFO") -> None:
    """Schreibt eine Hintergrund-Meldung ins Container-Log UND (falls eine
    DB-Session uebergeben wird) persistiert sie als SystemLogEvent, damit sie
    im "System Log" in der GUI sichtbar ist (siehe app.api.routes.logs) --
    vorher nur per print() ins Container-Log geschrieben und damit fuer die
    GUI nicht abrufbar."""
    print(f"[scheduler] {datetime.now(timezone.utc).isoformat()} {message}", flush=True)
    if db is not None:
        db.add(SystemLogEvent(level=level, source="scheduler", message=message))
        db.commit()


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
                _log(db, f"Health-Check fehlgeschlagen fuer Hyper-V-Cluster '{cluster.name}': {exc}", level="WARNING")
        for cluster in db.query(NetAppCluster).all():
            try:
                _refresh_netapp_status(db, cluster)
            except Exception as exc:
                _log(db, f"Health-Check fehlgeschlagen fuer NetApp-Cluster '{cluster.name}': {exc}", level="WARNING")
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
                _log(db, f"Discovery fehlgeschlagen fuer Hyper-V-Cluster '{cluster.name}': {exc}", level="WARNING")
        for cluster in db.query(NetAppCluster).all():
            try:
                _run_netapp_discovery(db, cluster)
            except Exception as exc:
                _log(db, f"Discovery fehlgeschlagen fuer NetApp-Cluster '{cluster.name}': {exc}", level="WARNING")
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
                _log(db, f"Snapshot-Abgleich uebersprungen fuer Cluster '{cluster.name}'/Volume '{volume_uuid}': {exc}", level="WARNING")
                continue

            for row in group_rows:
                if row.snapshot_name and row.snapshot_name not in real_names:
                    row.success = False
                    row.error_message = f"Snapshot wurde extern geloescht (Abgleich am {now_str} UTC)"
            db.commit()

        _reconcile_snapshot_destinations(db, rows, clusters)
    finally:
        _touch(db, "last_snapshot_reconciliation_at")
        db.close()


def _reconcile_snapshot_destinations(db: Session, rows: list[BackupRunSnapshot], clusters: dict[str, NetAppCluster]) -> None:
    """Zweiter Teil des Snapshot-Abgleichs: prueft fuer jeden (weiterhin)
    erfolgreichen Snapshot, ob er per SnapMirror auf eine discoverte
    Ziel-Beziehung repliziert wurde -- Grundlage fuer den Restore-von-
    SnapMirror-Destination-Workflow (siehe BackupRunSnapshotDestination).
    Snapshot-Namen bleiben beim SnapMirror-Transfer unveraendert (live
    verifiziert), der Abgleich erfolgt daher per Namensvergleich wie beim
    Quell-Abgleich oben."""
    clusters_by_name = {c.name: c for c in clusters.values()}
    now = datetime.now(timezone.utc)

    for row in rows:
        if not row.success or not row.snapshot_name or not row.svm_name or not row.volume_name:
            continue
        relationships = (
            db.query(NetAppSnapMirrorRelationship)
            .filter(NetAppSnapMirrorRelationship.source_path == f"{row.svm_name}:{row.volume_name}")
            .all()
        )
        if not relationships:
            continue

        existing_by_key = {(d.destination_svm_name, d.destination_volume_name): d for d in row.destinations}

        for rel in relationships:
            if not rel.destination_path or ":" not in rel.destination_path:
                continue
            dest_svm, dest_volume = rel.destination_path.split(":", 1)
            dest = existing_by_key.get((dest_svm, dest_volume))
            if dest is None:
                dest = BackupRunSnapshotDestination(
                    backup_run_snapshot_id=row.id, destination_svm_name=dest_svm, destination_volume_name=dest_volume,
                )
                db.add(dest)
            dest.relationship_uuid = rel.uuid
            dest.destination_netapp_cluster_name = rel.destination_cluster_name

            dest_cluster = clusters_by_name.get(rel.destination_cluster_name) if rel.destination_cluster_name else None
            if dest_cluster is None:
                # Ziel-Cluster nicht in dieser App registriert -- Praesenz
                # kann nicht live geprueft werden, letzter bekannter Stand
                # bleibt unveraendert stehen.
                continue
            dest.destination_netapp_cluster_id = dest_cluster.id

            dest_volume_row = (
                db.query(NetAppVolume)
                .filter(NetAppVolume.cluster_id == dest_cluster.id, NetAppVolume.svm_name == dest_svm, NetAppVolume.name == dest_volume)
                .first()
            )
            if dest_volume_row is None or not dest_volume_row.uuid:
                continue
            dest.destination_volume_uuid = dest_volume_row.uuid

            try:
                dest_service = _netapp_service_for(dest_cluster)
                dest_names = dest_service.list_snapshot_names(dest_volume_row.uuid)
            except Exception as exc:
                _log(db, f"Ziel-Abgleich uebersprungen fuer '{dest_svm}:{dest_volume}': {exc}", level="WARNING")
                continue
            dest.present = row.snapshot_name in dest_names
            dest.last_checked_at = now
        db.commit()


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
                    _log(db, f"Retention uebersprungen fuer Cluster '{cluster.name}': {exc}", level="WARNING")
                    continue

                for row in to_delete:
                    try:
                        result = service.delete_snapshot(row.volume_uuid, row.snapshot_uuid)
                    except Exception as exc:
                        _log(
                            db,
                            f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}') konnte nicht geloescht werden: {exc}",
                            level="ERROR",
                        )
                        continue
                    if not result.success:
                        _log(
                            db,
                            f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}') konnte nicht geloescht werden: {result.message}",
                            level="ERROR",
                        )
                        continue

                    # Vor dem Verwerfen der Zeile live pruefen, ob der
                    # Snapshot noch auf einem SnapMirror-Ziel vorhanden ist
                    # (nicht auf ggf. bis zu 24h alte BackupRunSnapshot-
                    # Destination-Daten verlassen -- direkt nachschauen).
                    # Ist er das, bleibt die Zeile (nur success=False, wie
                    # bei extern auf der Quelle geloeschten Snapshots) statt
                    # per Cascade auch die Ziel-Tracking-Infos zu verlieren
                    # -- sonst waere der Snapshot trotz noch vorhandener
                    # Kopie auf dem Sekundaersystem ploetzlich gar nicht mehr
                    # restorebar.
                    _reconcile_snapshot_destinations(db, [row], clusters)
                    if any(d.present for d in row.destinations):
                        row.success = False
                        row.error_message = (
                            f"Auf dem Primärsystem per Retention entfernt ({policy.retention_type.value}="
                            f"{policy.retention_value}) -- auf einem SnapMirror-Ziel weiterhin vorhanden."
                        )
                        _log(
                            db,
                            f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}') auf der Quelle "
                            "geloescht, bleibt aber ueber ein SnapMirror-Ziel restorebar.",
                        )
                    else:
                        db.delete(row)
                        _log(
                            db,
                            f"Retention: Snapshot '{row.snapshot_name}' (Policy '{policy.name}', "
                            f"Retention {policy.retention_type.value}={policy.retention_value}) geloescht",
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
                _log(db, f"Datei-Restore-Session fuer VM '{run.vm_name}' automatisch aufgeraeumt (Zeitlimit ueberschritten)")
            except Exception as exc:
                _log(db, f"Automatisches Aufraeumen der Datei-Restore-Session fuer VM '{run.vm_name}' fehlgeschlagen: {exc}", level="ERROR")
    finally:
        _touch(db, "last_file_restore_expiry_at")
        db.close()


def run_daily_email_summary() -> None:
    """Verschickt einmal taeglich (konfigurierbare lokale Stunde, siehe
    EmailConfig.daily_summary_hour) eine Zusammenfassung aller Backup-/
    Restore-/VM-Neuerstellungs-Laeufe der letzten 24 Stunden per E-Mail
    (Settings > E-Mail). Laeuft alle 15 Minuten (siehe start_scheduler) und
    prueft selbst, ob die konfigurierte Stunde erreicht UND heute noch
    keine Zusammenfassung verschickt wurde -- dadurch wirkt eine spaeter in
    der GUI geaenderte Uhrzeit sofort, ohne Container-Neustart (anders als
    ein fix bei start_scheduler registrierter CronTrigger)."""
    db = SessionLocal()
    try:
        config = db.query(EmailConfig).first()
        if config is None or not config.enabled or not config.daily_summary_enabled:
            return
        settings = get_settings()
        try:
            tz = ZoneInfo(settings.schedule_timezone)
        except Exception:
            tz = timezone.utc
        now_local = datetime.now(tz)
        if now_local.hour != config.daily_summary_hour:
            return
        today_label = now_local.strftime("%Y-%m-%d")
        status_row = db.query(SchedulerStatus).first()
        if status_row is not None and status_row.last_email_summary_sent_date == today_label:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = []
        failures = []

        backup_runs = db.query(BackupRun).filter(BackupRun.started_at >= cutoff).all()
        backup_failed = [r for r in backup_runs if r.status == JobStatus.FAILED]
        rows.append(DailySummaryRow("Backup-Laeufe", len(backup_runs), len(backup_runs) - len(backup_failed), len(backup_failed)))
        failures.extend(DailySummaryFailure("Backup", r.policy_name, r.error_message) for r in backup_failed)

        restore_runs = db.query(RestoreRun).filter(RestoreRun.started_at >= cutoff).all()
        restore_failed = [r for r in restore_runs if r.status == RestoreStatus.FAILED]
        rows.append(DailySummaryRow("Restore-Laeufe", len(restore_runs), len(restore_runs) - len(restore_failed), len(restore_failed)))
        failures.extend(DailySummaryFailure("Restore", r.vm_name, r.error_message) for r in restore_failed)

        recreate_runs = db.query(VmRecreateRun).filter(VmRecreateRun.started_at >= cutoff).all()
        recreate_failed = [r for r in recreate_runs if r.status == RestoreStatus.FAILED]
        rows.append(
            DailySummaryRow("VM-Neuerstellungen", len(recreate_runs), len(recreate_runs) - len(recreate_failed), len(recreate_failed))
        )
        failures.extend(DailySummaryFailure("VM-Neuerstellung", r.target_vm_name or r.vm_name, r.error_message) for r in recreate_failed)

        stats = DailySummaryStats(today_label, rows, failures)
        send_daily_summary(db, config, stats)

        if status_row is None:
            status_row = SchedulerStatus()
            db.add(status_row)
        status_row.last_email_summary_sent_date = today_label
        db.commit()
    finally:
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
            _log(db, f"Ungueltige HVNB_SCHEDULE_TIMEZONE '{settings.schedule_timezone}', falle auf UTC zurueck", level="WARNING")
            tz = timezone.utc
        now_local = datetime.now(tz)

        policies = db.query(BackupPolicy).filter(BackupPolicy.enabled.is_(True), BackupPolicy.schedule_id.isnot(None)).all()
        for policy in policies:
            schedule = db.get(Schedule, policy.schedule_id)
            if schedule is None or not _schedule_is_due(schedule, now_local):
                continue
            try:
                # _execute_job_run laeuft hier bewusst synchron (nicht als
                # Hintergrund-Task wie beim manuellen "Jetzt ausfuehren", siehe
                # trigger_job_run in jobs.py) -- run_scheduled_backups selbst
                # laeuft ja bereits im eigenen APScheduler-Hintergrund-Thread,
                # und max_instances=1 auf diesem Job (siehe start_scheduler)
                # verhindert ueberlappende Ausfuehrungen.
                run, warnings = _start_job_run(policy, db)
                _execute_job_run(run.id, warnings)
                _log(db, f"Geplanter Backup-Lauf gestartet: Policy '{policy.name}' (Zeitplan '{schedule.name}')")
            except Exception as exc:
                _log(db, f"Geplanter Backup-Lauf fuer Policy '{policy.name}' fehlgeschlagen: {exc}", level="ERROR")
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
    scheduler.add_job(
        run_daily_email_summary, IntervalTrigger(minutes=15),
        id="daily-email-summary", replace_existing=True, max_instances=1,
    )
    scheduler.start()
    startup_db = SessionLocal()
    try:
        _log(
            startup_db,
            f"gestartet (Health-Check alle {settings.healthcheck_interval_minutes}min, "
            f"Discovery alle {settings.discovery_interval_minutes}min, "
            f"Snapshot-Abgleich taeglich um {settings.snapshot_reconcile_hour:02d}:00 UTC, "
            f"Retention-Cleanup taeglich um {retention_hour:02d}:15 UTC, "
            f"geplante Backups minuetlich geprueft in Zeitzone {settings.schedule_timezone}, "
            f"Datei-Restore-Sicherheitsnetz stuendlich (Zeitlimit {settings.file_restore_max_age_hours}h), "
            f"E-Mail-Tageszusammenfassung alle 15min geprueft)",
        )
    finally:
        startup_db.close()
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
