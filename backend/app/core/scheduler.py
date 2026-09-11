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

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.routes.file_restore import _cleanup_file_restore_run
from app.api.routes.hyperv_clusters import _refresh_status as _refresh_hyperv_status
from app.api.routes.hyperv_clusters import _run_discovery as _run_hyperv_discovery
from app.api.routes.jobs import _execute_job_run, _occurrences_within, _start_job_run
from app.api.routes.netapp_clusters import _discover_and_persist as _run_netapp_discovery
from app.api.routes.netapp_clusters import _refresh_status as _refresh_netapp_status
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.alert import Alert, AlertConfig, AlertScope, AlertStatus, AlertType
from app.models.allowed_schedule_collision import AllowedScheduleCollision
from app.models.backup_policy import BackupPolicy, RetentionType
from app.models.backup_run import BackupRun, BackupRunSnapshot, BackupRunSnapshotDestination, BackupRunStep, JobStatus
from app.models.email_config import EmailConfig
from app.models.file_restore_run import FileRestoreRun
from app.models.hyperv_cluster import HyperVCluster, HyperVClusterHealth
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.netapp_cluster import NetAppCluster, NetAppClusterHealth
from app.models.netapp_discovery import NetAppLun, NetAppSnapMirrorRelationship, NetAppVolume
from app.models.resource_group import ResourceGroupPolicyLink
from app.models.restore_run import RestoreRun, RestoreStatus, RestoreStepStatus
from app.models.schedule import ScheduleType
from app.models.scheduler_config import SchedulerConfig
from app.models.scheduler_status import SchedulerStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_recreate_run import VmRecreateRun
from app.services.email_service import DailySummaryFailure, DailySummaryRow, DailySummaryStats, send_daily_summary

_scheduler: BackgroundScheduler | None = None

# Fester Ankerpunkt fuer ALLE intervallbasierten Jobs (IntervalTrigger).
# Ohne start_date berechnet IntervalTrigger den naechsten Lauf als "jetzt +
# Intervall" ab dem Moment der (Neu-)Registrierung -- d.h. bei JEDEM
# Prozess-Neustart (z.B. durch einen Deploy) wird die Uhr neu gestartet.
# Bei kurzen Intervallen (Health-Check/Alert-Check, wenige Minuten) faellt
# das kaum auf, bei langen (Discovery, Default 240min) kann ein Prozess,
# der oefter als einmal pro Intervall neu startet, den Job dadurch DAUERHAFT
# verhungern lassen, statt ihn nur zu verzoegern -- real beobachtet am
# 2026-09-07 (Discovery lief zwischen 05:34 UTC und einem manuellen Anstoss
# kein einziges Mal mehr, trotz 240min-Intervall, weil der Prozess durch
# laufende Deploys elfmal in der Zwischenzeit neu gestartet ist, siehe
# app-feature-backlog-Memory). Ein fester Ankerpunkt in der Vergangenheit
# sorgt dafuer, dass IntervalTrigger den naechsten Lauf immer auf demselben
# festen Zeitraster (Anker + n*Intervall) berechnet -- unabhaengig davon,
# wie oft der Prozess dazwischen neu gestartet wird. Muss bei JEDER
# IntervalTrigger-Instanziierung fuer einen periodischen Job mitgegeben
# werden (Erstregistrierung in start_scheduler UND jedes spaetere
# reschedule_job, siehe app.api.routes.scheduler_config/alerts).
INTERVAL_ANCHOR = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Eigener, leicht versetzter Anker NUR fuer die volle Discovery: mit
# INTERVAL_ANCHOR direkt tickt sie exakt auf volle, durch das Intervall
# teilbare Stunden (z.B. 240min -> 00/04/08/12/16/20:00 UTC) -- dieselben
# Vielfachen, auf die ueblicherweise auch stuendliche/mehrstuendliche
# Backup-Zeitplaene gelegt werden (z.B. "alle 2h" -> ebenfalls :00). Beide
# kollidieren dadurch STRUKTURELL, nicht nur zufaellig: trifft eine volle
# Discovery exakt ein offenes Checkpoint-Fenster, friert sie den VHD-
# Zwischenstand (AVHDX statt VHDX) bis zur naechsten vollen Discovery ein
# (live beobachtet 2026-09-11: RestoreTestVM_PG2/VM03, last_seen_at exakt
# 04:00:23 UTC -- echter Knotenzustand zu dem Zeitpunkt laengst wieder
# VHDX). +7min liegt ausserhalb der in der Praxis beobachteten Zeitplan-
# Minuten (:00/:10/:15) und reduziert damit die strukturelle
# Kollisionswahrscheinlichkeit fuer ALLE discoverten Felder, nicht nur
# VHD-Pfade. Siehe auch den Einzel-VM-Refresh nach Checkpoint-Entfernung in
# _execute_job_run (jobs.py) fuer den zweiten, primaeren Teil dieses Fixes.
DISCOVERY_INTERVAL_ANCHOR = INTERVAL_ANCHOR + timedelta(minutes=7)


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
        _log(db, "Task gestartet: Health-Check")
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


# Wie oft eine faellige Discovery maximal um je 5min verschoben wird, wenn
# gerade ein Backup-Lauf aktiv ist (siehe run_discovery). Danach laeuft die
# Discovery trotzdem, damit das Inventory (u.a. Grundlage der Kapazitaets-
# Alarme) nicht beliebig lange veraltet -- ein Backup, das laenger als
# ~25min laeuft, ist ohnehin ein Fall fuer force_cancel_timed_out_runs.
_DISCOVERY_MAX_DEFERRALS = 5


def run_discovery(deferred: int = 0) -> None:
    db = SessionLocal()
    try:
        # Discovery und ein laufender Backup-Lauf duerfen sich NICHT
        # ueberschneiden: beide fahren pro VM dieselben schweren WinRM-
        # Aufrufe (Get-VHD ueber die gesamte AVHDX-Kette). Live 2026-09-09
        # beobachtet: eine :00-Discovery lief zeitgleich mit dem stuendlichen
        # :00-Backup, beide auf Get-VHD derselben -- durch einen laufenden
        # Checkpoint-Merge gesperrten -- Kette; der Backup-Lauf blieb daran
        # haengen (pywinrm pollt einen langlaufenden Aufruf unbegrenzt
        # weiter). Statt starrem Takt: laeuft gerade ein Backup, diese
        # Discovery-Runde ueberspringen und in 5min erneut versuchen (bis
        # _DISCOVERY_MAX_DEFERRALS, danach trotzdem laufen).
        if deferred < _DISCOVERY_MAX_DEFERRALS and db.query(BackupRun.id).filter(
            BackupRun.status == JobStatus.RUNNING
        ).first():
            _log(
                db,
                f"Discovery verschoben -- Backup-Lauf aktiv, neuer Versuch in 5min "
                f"(Verschiebung {deferred + 1}/{_DISCOVERY_MAX_DEFERRALS})",
            )
            scheduler = get_scheduler()
            if scheduler is not None:
                scheduler.add_job(
                    run_discovery, "date",
                    run_date=datetime.now(timezone.utc) + timedelta(minutes=5),
                    args=[deferred + 1],
                    id="discovery-deferred", replace_existing=True, max_instances=1,
                )
            return
        _log(db, "Task gestartet: Discovery")
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
        _log(db, "Task gestartet: Snapshot-Abgleich")
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
        _log(db, "Task gestartet: Retention-Cleanup")
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


_LAG_TIME_PATTERN = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _parse_lag_minutes(lag_time: str | None) -> int | None:
    """Parst ONTAPs ISO-8601-Dauerformat (z.B. 'P1DT2H30M') zu Gesamtminuten
    -- dasselbe Format, das formatLagTime() im Frontend fuer die Anzeige
    parst (frontend/src/utils/format.ts), hier fuer den Schwellwert-Vergleich
    in run_alert_check."""
    if not lag_time:
        return None
    m = _LAG_TIME_PATTERN.match(lag_time)
    if not m:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return days * 24 * 60 + hours * 60 + minutes + seconds // 60


def _hyperv_referenced_keys(db: Session) -> tuple[set[str], set[tuple[str, str, str]]]:
    """Ermittelt, welche NetApp-LUNs/-Volumes tatsaechlich als Hyper-V-
    Storage genutzt werden -- fuer AlertScope.HYPERV_REFERENCED in
    run_alert_check. Liefert (referenzierte LUN-IDs, referenzierte
    (cluster_id, svm_name, volume_name)-Tripel).

    Matcht bewusst ueber HyperVCsv.disk_serial_number <-> NetAppLun.
    serial_number (dieselbe stabile Windows-Disk-Seriennummer, ueber die
    auch die urspruengliche Zuordnung beim Hyper-V-Discovery-Lauf erfolgt,
    siehe hyperv_clusters.py discover_cluster) -- NICHT ueber
    HyperVCsv.netapp_lun_id. Dieses Feld verweist auf NetAppLun.id, die
    interne Datenbank-ID, die bei JEDER NetApp-LUN-Discovery komplett neu
    vergeben wird (Loeschen + Neuanlegen aller Zeilen). Ein reiner
    ID-Abgleich lieferte dadurch faelschlich eine leere Referenzmenge,
    sobald eine NetApp-Discovery zwischen zwei Hyper-V-Discovery-Laeufen
    lag -- live vom Nutzer aufgedeckt (Schwellwert 75% richtig konfiguriert,
    aber trotz Volumes/LUNs bei 80% keine einzige Warnung)."""
    referenced_serials = {
        c.disk_serial_number for c in db.query(HyperVCsv).filter(HyperVCsv.disk_serial_number.isnot(None)).all()
    }
    referenced_lun_ids: set[str] = set()
    referenced_volume_keys: set[tuple[str, str, str]] = set()
    if referenced_serials:
        for lun in db.query(NetAppLun).filter(NetAppLun.serial_number.in_(referenced_serials)).all():
            referenced_lun_ids.add(lun.id)
            if lun.svm_name and lun.volume_name:
                referenced_volume_keys.add((lun.cluster_id, lun.svm_name, lun.volume_name))
    return referenced_lun_ids, referenced_volume_keys


def _schedule_day_key(schedule) -> tuple:
    """Reduziert einen Zeitplan auf die Tage, an denen er ueberhaupt feuern
    kann -- fuer den Kollisions-Check in _find_schedule_collisions. HOURLY/
    DAILY feuern jeden Tag (('ANY',), kompatibel mit allem), WEEKLY nur am
    eingestellten Wochentag, MONTHLY nur am eingestellten Monatstag."""
    if schedule.schedule_type == ScheduleType.WEEKLY:
        return ("WEEKDAY", schedule.weekday)
    if schedule.schedule_type == ScheduleType.MONTHLY:
        return ("MONTHDAY", schedule.day_of_month)
    return ("ANY",)


def _days_compatible(day_key_a: tuple, day_key_b: tuple) -> bool:
    if day_key_a[0] == "ANY" or day_key_b[0] == "ANY":
        return True
    return day_key_a == day_key_b


def _circular_minute_distance(a: int, b: int) -> int:
    diff = abs(a - b) % 1440
    return min(diff, 1440 - diff)


def _find_schedule_collisions(
    links: list[ResourceGroupPolicyLink], threshold_minutes: int
) -> list[dict]:
    """Findet Zeitplan-Kollisionen rein aus der Konfiguration heraus, ohne
    Vorkommen ueber ein Datumsfenster zu simulieren (anders als
    _occurrences_within/BACKUP_MISSED oben) -- dadurch werden auch seltene
    Kombinationen zuverlaessig erkannt, z.B. ein monatlicher Lauf am 15.,
    der nur an diesem einen Tag im Monat mit einem taeglichen Lauf
    kollidiert, ohne dass dafuer ein 30+ Tage weites Vorschau-Fenster noetig
    waere.

    Zwei Zeitplan-Zeiten 'kollidieren', wenn sie (a) an einem gemeinsam
    moeglichen Kalendertag feuern koennen (siehe _days_compatible -- WEEKLY
    x MONTHLY wird dabei bewusst NIE als kompatibel gewertet, da eine
    Ueberschneidung nur zufaellig in manchen Monaten vorkommt und dafuer zu
    selten/verwirrend waere) UND (b) ihr Uhrzeit-Abstand (zirkulaer ueber
    Mitternacht hinweg) innerhalb von threshold_minutes liegt.

    Kollidierende Zeiten werden per Union-Find zu Clustern zusammengefasst
    (>= 2 Mitglieder). Das ist eine Vereinfachung: bei genau drei
    unterschiedlichen Kadenzen, die nur ueber ein gemeinsames drittes
    Mitglied verbunden sind (z.B. ein taeglicher Lauf, der sowohl mit einem
    woechentlichen als auch einem an sich inkompatiblen monatlichen Lauf
    kollidiert), landen alle drei in einem Cluster, obwohl die aeusseren
    beiden fuer sich genommen nicht kollidieren -- in der weit
    ueberwiegenden Praxis (gleiche oder aehnliche Kadenzen) liefert das
    trotzdem das richtige Ergebnis und vermeidet den Aufwand einer vollen
    Cliquen-Suche.

    Jeder zurueckgegebene Cluster traegt einen stabilen `key`
    (ausschliesslich aus resource_group_id/policy_id/Uhrzeit abgeleitet,
    unabhaengig vom aktuellen Datum) -- Grundlage sowohl fuer die
    Alert.object_key-Wiedererkennung als auch fuer AllowedScheduleCollision.
    """
    slots: list[dict] = []
    for link in links:
        schedule = link.schedule
        policy = link.policy
        group = link.resource_group
        if schedule is None or policy is None or group is None or not schedule.times or not policy.enabled:
            continue
        day_key = _schedule_day_key(schedule)
        for time_str in schedule.times:
            try:
                hour, minute = (int(p) for p in time_str.split(":"))
            except ValueError:
                continue
            slots.append(
                {
                    "minutes": hour * 60 + minute,
                    "time_str": time_str,
                    "day_key": day_key,
                    "rg_id": group.id,
                    "rg_name": group.name,
                    "policy_id": policy.id,
                    "policy_name": policy.name,
                }
            )

    parent = list(range(len(slots)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            if not _days_compatible(slots[i]["day_key"], slots[j]["day_key"]):
                continue
            if _circular_minute_distance(slots[i]["minutes"], slots[j]["minutes"]) <= threshold_minutes:
                union(i, j)

    clusters: dict[int, list[dict]] = defaultdict(list)
    for i, slot in enumerate(slots):
        clusters[find(i)].append(slot)

    results: list[dict] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda s: (s["minutes"], s["rg_id"], s["policy_id"]))
        key = "|".join(f"{m['rg_id']}:{m['policy_id']}:{m['time_str']}" for m in members_sorted)
        summary = ", ".join(f"[{m['rg_name']}] {m['policy_name']} um {m['time_str']} Uhr" for m in members_sorted)
        results.append({"key": key, "members": members_sorted, "summary": summary})
    return results


def run_alert_check() -> None:
    """Prueft die Bedingungen, die im Dashboard unter 'Warnungen' gezaehlt
    werden (siehe app.api.routes.alerts): Kapazitaets-Schwellwerte (Volume/
    LUN, je Kategorie eigener Schwellwert), ungesunde Hyper-V-/NetApp-
    Cluster, ungesunde SnapMirror-Beziehungen sowie ueberschrittene
    SnapMirror-Lag-Time. Scope-Einstellung (AlertConfig.scope) filtert
    Volumes/LUNs/SnapMirror-Beziehungen optional auf die tatsaechlich vom
    Hyper-V-Cluster genutzten (siehe _hyperv_referenced_keys) -- Cluster-
    Gesundheit ist davon nicht betroffen (ein Cluster ist immer relevant).
    Nutzt ausschliesslich bereits durch Health-Check/Discovery aktualisierte
    DB-Werte (kein eigener NetApp-/WinRM-Aufruf hier) -- legt bei neu
    erkannten Verstoessen einen Alert an und markiert nicht mehr
    zutreffende als resolved (object_key verhindert doppelte aktive Alarme
    fuer dasselbe Objekt). Fehlgeschlagene Backup-Laeufe sind bewusst NICHT
    Teil dieses Checks, siehe app.models.alert.

    Prueft ausserdem verpasste geplante Laeufe (BACKUP_MISSED, bewusst NICHT
    Teil der automatischen Aufloesung) sowie Zeitplan-Kollisionen
    (SCHEDULE_COLLISION, siehe _find_schedule_collisions -- IST Teil der
    automatischen Aufloesung, aber vom Nutzer bestaetigte Kollisionen werden
    dauerhaft uebersprungen, siehe AllowedScheduleCollision) sowie verwaiste
    Hyper-V-Checkpoints (HYPERV_ORPHAN_CHECKPOINT, IST Teil der
    automatischen Aufloesung -- verschwindet der Checkpoint, egal ob durch
    Loeschen ueber die GUI oder anderweitig, aus der naechsten Discovery,
    loest sich der Alarm von selbst) sowie VMs, deren VHDX ueber mehrere
    CSVs verteilt liegen (HYPERV_VM_MULTI_CSV, ebenfalls Teil der
    automatischen Aufloesung, bewusst ohne Karenzzeit)."""
    db = SessionLocal()
    try:
        # Bewusst KEIN "Task gestartet"-Log hier (anders als Health-Check/
        # Discovery/Snapshot-Abgleich/Retention-Cleanup): laeuft alle 15min,
        # ist aber in der ganz ueberwiegenden Mehrzahl der Laeufe ein reiner
        # No-Op (nichts ueber-/unterschritten) -- 96 Log-Zeilen/Tag ohne
        # Mehrwert. _trigger() unten sowie die Aufloesungs-Schleife loggen
        # bereits gezielt, wann tatsaechlich ein Alarm ausgeloest/aufgeloest
        # wird (Nutzer-Rueckmeldung, siehe dieselbe Begruendung bei
        # run_daily_email_summary/run_file_restore_expiry weiter unten).
        config = db.query(AlertConfig).first()
        vol_threshold = config.volume_threshold_percent if config else 90
        lun_threshold = config.lun_threshold_percent if config else 90
        lag_threshold_hours = config.snapmirror_lag_threshold_hours if config else 4
        scope = config.scope if config else AlertScope.ALL
        now = datetime.now(timezone.utc)

        referenced_lun_ids, referenced_volume_keys = (
            _hyperv_referenced_keys(db) if scope == AlertScope.HYPERV_REFERENCED else (None, None)
        )

        active_by_key: dict[tuple[AlertType, str], Alert] = {
            (a.alert_type, a.object_key): a for a in db.query(Alert).filter(Alert.status == AlertStatus.ACTIVE).all()
        }
        seen_keys: set[tuple[AlertType, str]] = set()
        netapp_cluster_names = {c.id: c.name for c in db.query(NetAppCluster).all()}

        def _trigger(alert_type: AlertType, key: str, **kwargs) -> None:
            db.add(Alert(alert_type=alert_type, object_key=key, status=AlertStatus.ACTIVE, triggered_at=now, **kwargs))
            _log(db, f"Neue Warnung ({alert_type.value}): {kwargs.get('object_name')} -- {kwargs.get('message')}", level="WARNING")

        for vol in db.query(NetAppVolume).filter(NetAppVolume.percent_used.isnot(None)).all():
            if referenced_volume_keys is not None and (vol.cluster_id, vol.svm_name, vol.name) not in referenced_volume_keys:
                continue
            if vol.percent_used < vol_threshold:
                continue
            key = vol.uuid or f"{vol.cluster_id}:{vol.name}"
            seen_keys.add((AlertType.CAPACITY_VOLUME, key))
            if (AlertType.CAPACITY_VOLUME, key) not in active_by_key:
                _trigger(
                    AlertType.CAPACITY_VOLUME, key, object_name=vol.name,
                    netapp_cluster_id=vol.cluster_id, netapp_cluster_name=netapp_cluster_names.get(vol.cluster_id),
                    svm_name=vol.svm_name, message=f"Volume zu {vol.percent_used}% belegt (Schwellwert {vol_threshold}%)",
                    threshold_percent=vol_threshold, triggered_percent=vol.percent_used,
                )

        for lun in db.query(NetAppLun).filter(NetAppLun.used_bytes.isnot(None), NetAppLun.size_bytes.isnot(None)).all():
            if referenced_lun_ids is not None and lun.id not in referenced_lun_ids:
                continue
            if not lun.size_bytes:
                continue
            percent = round(lun.used_bytes / lun.size_bytes * 100)
            if percent < lun_threshold:
                continue
            key = lun.uuid or f"{lun.cluster_id}:{lun.name}"
            seen_keys.add((AlertType.CAPACITY_LUN, key))
            if (AlertType.CAPACITY_LUN, key) not in active_by_key:
                _trigger(
                    AlertType.CAPACITY_LUN, key, object_name=lun.name,
                    netapp_cluster_id=lun.cluster_id, netapp_cluster_name=netapp_cluster_names.get(lun.cluster_id),
                    svm_name=lun.svm_name, message=f"LUN zu {percent}% belegt (Schwellwert {lun_threshold}%)",
                    threshold_percent=lun_threshold, triggered_percent=percent,
                )

        for cluster in db.query(HyperVCluster).all():
            if cluster.health == HyperVClusterHealth.HEALTHY:
                continue
            seen_keys.add((AlertType.HYPERV_CLUSTER_UNHEALTHY, cluster.id))
            if (AlertType.HYPERV_CLUSTER_UNHEALTHY, cluster.id) not in active_by_key:
                _trigger(
                    AlertType.HYPERV_CLUSTER_UNHEALTHY, cluster.id, object_name=cluster.name,
                    hyperv_cluster_id=cluster.id, message=f"Cluster-Status: {cluster.health.value}",
                )

        for cluster in db.query(HyperVCluster).all():
            for node in cluster.unreachable_nodes:
                node_name = node.get("name") or "?"
                key = f"{cluster.id}:{node_name}"
                seen_keys.add((AlertType.HYPERV_NODE_UNREACHABLE, key))
                if (AlertType.HYPERV_NODE_UNREACHABLE, key) not in active_by_key:
                    error = node.get("error") or "nicht erreichbar"
                    _trigger(
                        AlertType.HYPERV_NODE_UNREACHABLE, key, object_name=f"{node_name} ({cluster.name})",
                        hyperv_cluster_id=cluster.id,
                        message=f"Knoten per WinRM nicht erreichbar: {error}",
                    )

        for cluster in db.query(NetAppCluster).all():
            if cluster.health == NetAppClusterHealth.HEALTHY:
                continue
            seen_keys.add((AlertType.NETAPP_CLUSTER_UNHEALTHY, cluster.id))
            if (AlertType.NETAPP_CLUSTER_UNHEALTHY, cluster.id) not in active_by_key:
                _trigger(
                    AlertType.NETAPP_CLUSTER_UNHEALTHY, cluster.id, object_name=cluster.name,
                    netapp_cluster_id=cluster.id, netapp_cluster_name=cluster.name,
                    message=f"Cluster-Status: {cluster.health.value}",
                )

        def _relationship_referenced(rel: NetAppSnapMirrorRelationship) -> bool:
            if referenced_volume_keys is None:
                return True
            for path in (rel.source_path, rel.destination_path):
                if not path or ":" not in path:
                    continue
                svm, volume = path.split(":", 1)
                if (rel.cluster_id, svm, volume) in referenced_volume_keys:
                    return True
            return False

        for rel in db.query(NetAppSnapMirrorRelationship).all():
            if not _relationship_referenced(rel):
                continue
            name = f"{rel.source_path or '?'} -> {rel.destination_path or '?'}"

            if not rel.healthy:
                key = rel.uuid or rel.id
                seen_keys.add((AlertType.SNAPMIRROR_UNHEALTHY, key))
                if (AlertType.SNAPMIRROR_UNHEALTHY, key) not in active_by_key:
                    _trigger(
                        AlertType.SNAPMIRROR_UNHEALTHY, key, object_name=name,
                        netapp_cluster_id=rel.cluster_id, netapp_cluster_name=netapp_cluster_names.get(rel.cluster_id),
                        message=f"SnapMirror-Beziehung ungesund (Status: {rel.state or 'unbekannt'})",
                    )

            lag_minutes = _parse_lag_minutes(rel.lag_time)
            if lag_minutes is not None and lag_minutes >= lag_threshold_hours * 60:
                lag_hours_actual = round(lag_minutes / 60)
                key = f"lag:{rel.uuid or rel.id}"
                seen_keys.add((AlertType.SNAPMIRROR_LAG_EXCEEDED, key))
                if (AlertType.SNAPMIRROR_LAG_EXCEEDED, key) not in active_by_key:
                    _trigger(
                        AlertType.SNAPMIRROR_LAG_EXCEEDED, key, object_name=name,
                        netapp_cluster_id=rel.cluster_id, netapp_cluster_name=netapp_cluster_names.get(rel.cluster_id),
                        message=f"SnapMirror-Lag {lag_hours_actual}h (Schwellwert {lag_threshold_hours}h)",
                        threshold_percent=lag_threshold_hours, triggered_percent=lag_hours_actual,
                    )

        # Verpasste geplante Backups (vom Nutzer am 2026-09-03 gewuenscht,
        # nachdem ein WSL2-Neustart ueber Nacht mehrere faellige Laeufe
        # stillschweigend ausfallen liess -- der Nachhol-Mechanismus in
        # run_scheduled_backups deckt bewusst nur kurze Ueberlappungen ab
        # (15min-Fenster), NICHT eine echte mehrstuendige Downtime). Prueft
        # rueckwirkend ueber die letzten 48h, ob fuer jedes faellige
        # Vorkommen einer Resource-Group-Policy-Verknuepfung tatsaechlich
        # ein BackupRun existiert -- kein eigener Scheduler-Job noetig,
        # laeuft hier rein DB-basiert mit im bereits alle 15min laufenden
        # Warnungs-Check mit. Bewusst NICHT Teil der automatischen
        # Aufloesung oben (seen_keys) -- ein verpasster Lauf ist eine
        # abgeschlossene historische Tatsache, kein Zustand, der sich von
        # selbst wieder 'gesund' meldet; bleibt daher aktiv, bis der Nutzer
        # ihn manuell quittiert (POST /alerts/{id}/dismiss) oder per "Jetzt
        # nachholen" nachtraegt.
        try:
            schedule_tz = ZoneInfo(get_settings().schedule_timezone)
        except Exception:
            schedule_tz = timezone.utc
        grace_minutes = config.backup_missed_grace_minutes if config else 30
        now_local_missed = datetime.now(schedule_tz)
        lookback_start = now_local_missed - timedelta(hours=48)
        cutoff_local = now_local_missed - timedelta(minutes=grace_minutes)

        links = db.query(ResourceGroupPolicyLink).filter(ResourceGroupPolicyLink.schedule_id.isnot(None)).all()
        missed_occurrences_by_schedule: dict[tuple[str, datetime], list[datetime]] = {}
        for link in links:
            schedule = link.schedule
            policy = link.policy
            group = link.resource_group
            if schedule is None or policy is None or group is None or not schedule.times or not policy.enabled:
                continue
            # Live gefunden: ein Zeitplan kann juenger sein als das 48h-
            # Rueckblickfenster (z.B. erst gestern angelegt) -- ohne diese
            # Untergrenze wuerden Vorkommen VOR seiner Erstellung faelschlich
            # als 'verpasst' gemeldet, obwohl der Zeitplan zu dem Zeitpunkt
            # schlicht noch nicht existierte. ResourceGroupPolicyLink selbst
            # hat (zusammengesetzter Primärschluessel, kein eigenes id/
            # created_at) keinen exakteren Anhaltspunkt fuer den Zeitpunkt
            # der Verknuepfung -- Schedule.created_at ist die beste
            # verfuegbare Naeherung.
            window_start = max(lookback_start, schedule.created_at.astimezone(schedule_tz))
            cache_key = (schedule.id, window_start)
            if cache_key not in missed_occurrences_by_schedule:
                missed_occurrences_by_schedule[cache_key] = _occurrences_within(schedule, window_start, now_local_missed)
            for occurrence_local in missed_occurrences_by_schedule[cache_key]:
                if occurrence_local > cutoff_local:
                    continue  # noch innerhalb der Karenzzeit -- normale Verzoegerung, kein Fehlalarm
                occurrence_utc = occurrence_local.astimezone(timezone.utc)
                # Backlog-Punkt 36 (Nutzer-Vorgabe 2026-09-09): waehrend
                # einer (ggf. inzwischen beendeten) Pause der Protection
                # Group ausgelassene Vorkommen sind bewusst uebersprungen,
                # nicht verpasst -- paused_until bleibt waehrend einer noch
                # laufenden Pause leer (Fenster reicht dann bis "jetzt").
                if (
                    group.paused_since is not None
                    and occurrence_utc >= group.paused_since
                    and (group.paused_until is None or occurrence_utc <= group.paused_until)
                ):
                    continue
                # ResourceGroupPolicyLink hat keinen eigenen Primärschluessel
                # (zusammengesetzt aus resource_group_id+policy_id) -- beide
                # zusammen identifizieren die Verknuepfung eindeutig.
                key = f"{link.resource_group_id}:{link.policy_id}:{occurrence_utc.isoformat()}"
                if (AlertType.BACKUP_MISSED, key) in active_by_key:
                    continue  # bereits gemeldet
                matching_run = (
                    db.query(BackupRun)
                    .filter(
                        BackupRun.policy_id == policy.id,
                        # resource_group_id ist NULL bei einem manuellen
                        # 'Jetzt ausfuehren' auf der ganzen Policy (deckt
                        # dann automatisch auch diese Gruppe mit ab) sowie
                        # bei jedem Lauf von VOR der Resource-Group-
                        # Verknuepfungs-Funktion (aeltere Bestandsdaten) --
                        # beides zaehlt als 'nicht verpasst'.
                        or_(BackupRun.resource_group_id == group.id, BackupRun.resource_group_id.is_(None)),
                        BackupRun.started_at >= occurrence_utc,
                        BackupRun.started_at <= occurrence_utc + timedelta(minutes=grace_minutes),
                    )
                    .first()
                )
                if matching_run is not None:
                    continue
                _trigger(
                    AlertType.BACKUP_MISSED, key,
                    object_name=f"{group.name} / {policy.name}",
                    message=f"Geplanter Lauf verpasst: faellig {occurrence_local.strftime('%Y-%m-%d %H:%M')} (Zeitplan '{schedule.name}')",
                    resource_group_id=group.id, policy_id=policy.id,
                )

        # Zeitplan-Kollisionen (Backlog-Punkt 17, GUI-Warnung -- die
        # zugrundeliegende Korrektheits-Luecke, ein stillschweigend
        # uebersprungener Scheduler-Tick, wurde bereits am 2026-09-02 per
        # Nachhol-Mechanismus behoben, siehe run_scheduled_backups). Anders
        # als backup_missed IST dies ein sich selbst aufloesender Zustand
        # (rein aus der aktuellen Konfiguration abgeleitet, keine
        # historische Tatsache) -- daher normaler Teil der seen_keys-
        # Aufloesungsschleife unten. Einmal vom Nutzer bestaetigte
        # Kollisionen (siehe POST /alerts/{id}/allow-collision) werden
        # dauerhaft uebersprungen, bis sich ihre genaue Zusammensetzung
        # aendert (siehe AllowedScheduleCollision).
        collision_window_minutes = config.schedule_collision_window_minutes if config else 15
        allowed_collision_keys = {row[0] for row in db.query(AllowedScheduleCollision.collision_key).all()}
        for cluster in _find_schedule_collisions(links, collision_window_minutes):
            key = cluster["key"]
            seen_keys.add((AlertType.SCHEDULE_COLLISION, key))
            if key in allowed_collision_keys:
                continue
            if (AlertType.SCHEDULE_COLLISION, key) not in active_by_key:
                _trigger(
                    AlertType.SCHEDULE_COLLISION, key,
                    object_name="Zeitplan-Kollision",
                    message=f"{len(cluster['members'])} Job-Starts liegen innerhalb von {collision_window_minutes} Minuten: {cluster['summary']}",
                )

        # Verwaiste Hyper-V-Checkpoints (Nutzer-Vorgabe 2026-09-07): ein
        # Backup-Job kann abbrechen, bevor der von ihm erstellte Checkpoint
        # wieder entfernt wird -- _execute_job_run haelt die Liste der aktiven
        # Checkpoints nur im Prozessspeicher (active_checkpoints), ein harter
        # Absturz zwischen Erstellung und Entfernung hinterlaesst dazu nichts
        # Persistiertes. Erkennung daher rein ueber die naechste Discovery
        # (Get-VMSnapshot, siehe HyperVService.list_vms) statt ueber eine
        # Live-Lauf-Pruefung -- ein normaler Checkpoint besteht nur Sekunden
        # bis wenige Minuten, eine Karenzzeit auf Basis des Checkpoint-Alters
        # reicht daher aus, um ihn von einem gerade laufenden Backup zu
        # unterscheiden, ohne BackupRun/VM gegeneinander abgleichen zu muessen.
        checkpoint_grace_minutes = config.orphan_checkpoint_grace_minutes if config else 60
        hyperv_cluster_names = {c.id: c.name for c in db.query(HyperVCluster).all()}
        checkpoint_cutoff = now - timedelta(minutes=checkpoint_grace_minutes)
        for vm in db.query(HyperVVm).filter(HyperVVm.checkpoints.isnot(None)).all():
            for cp in vm.checkpoints or []:
                try:
                    created_at = datetime.fromisoformat(cp["creation_time"])
                except (KeyError, ValueError, TypeError):
                    continue
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at > checkpoint_cutoff:
                    continue  # noch innerhalb der Karenzzeit -- vermutlich ein normaler, noch laufender Backup-Checkpoint
                key = cp["id"]
                seen_keys.add((AlertType.HYPERV_ORPHAN_CHECKPOINT, key))
                is_app_created = cp["name"].startswith("hvnb_")
                origin = "vermutlich von einem abgebrochenen Backup-Lauf" if is_app_created else "manuell erstellt"
                created_utc = created_at.astimezone(timezone.utc)
                age_h, age_m = divmod(max(0, int((now - created_utc).total_seconds()) // 60), 60)
                age_str = f"{age_h} h {age_m} min" if age_h else f"{age_m} min"
                # Absolute Erstellzeit als stabiler Anker PLUS relative
                # Angabe, die bei JEDEM Lauf frisch gesetzt wird. Frueher
                # stand nur "seit N Minuten", einmalig bei Erst-Erkennung
                # berechnet und nie aktualisiert -- dadurch zeigten mehrere
                # Stunden auseinander liegende Checkpoints alle denselben
                # eingefrorenen Wert (Nutzer-Rueckmeldung: "beide melden seit
                # 244 Minuten, obwohl der eine von 8:00, der andere von 12:00
                # ist"). Grund fuer die immer gleichen 244: Karenzzeit
                # (Default 60min) + bis zu ein volles Discovery-Intervall
                # (Default 240min) bis zur Erst-Erkennung.
                message = (
                    f"Checkpoint '{cp['name']}' existiert seit {created_utc:%d.%m.%Y %H:%M} UTC "
                    f"(vor {age_str}), {origin}, Cluster {hyperv_cluster_names.get(vm.cluster_id, '?')}"
                )
                existing = active_by_key.get((AlertType.HYPERV_ORPHAN_CHECKPOINT, key))
                if existing is None:
                    _trigger(
                        AlertType.HYPERV_ORPHAN_CHECKPOINT, key,
                        object_name=f"{vm.name} / {cp['name']}",
                        hyperv_cluster_id=vm.cluster_id,
                        vm_name=vm.name,
                        checkpoint_id=cp["id"],
                        message=message,
                    )
                elif existing.message != message:
                    existing.message = message  # relative Altersangabe aktuell halten

        # VM mit VHDX auf mehreren CSVs verteilt (Nutzer-Vorgabe 2026-09-09,
        # Backlog-Punkt 35): eine CSV-scope Protection Group deckt nur EIN
        # CSV ab -- verteilt eine VM ihre Disks ueber mehrere, schuetzt eine
        # einzelne CSV-Policy dann nur einen Teil dieser VM. Rein aus den
        # bereits discoverten HyperVVhd-Zeilen abgeleitet (kein WinRM-Aufruf
        # hier), pro VM eindeutig ueber die stabile Hyper-V-VM-GUID
        # (vm_uuid) -- KEIN Cluster-Praefix im object_key noetig (anders als
        # z.B. bei CSV-/VM-Namen, siehe _csv_index in jobs.py), da diese GUID
        # bereits global eindeutig ist. Bewusst OHNE Karenzzeit (anders als
        # beim Checkpoint-Alarm, Nutzer-Entscheidung): eine kurze,
        # tatsaechliche Storage-Live-Migration einer einzelnen VHDX zwischen
        # zwei CSVs loest den Alarm beim naechsten Discovery-Lauf ohnehin von
        # selbst wieder auf.
        csv_names_by_vm: dict[str, set[str]] = defaultdict(set)
        vm_name_by_uuid: dict[str, str] = {}
        cluster_by_uuid: dict[str, str] = {}
        for vhd in db.query(HyperVVhd).filter(HyperVVhd.vm_uuid.isnot(None), HyperVVhd.csv_name.isnot(None)).all():
            csv_names_by_vm[vhd.vm_uuid].add(vhd.csv_name)
            vm_name_by_uuid[vhd.vm_uuid] = vhd.vm_name
            cluster_by_uuid[vhd.vm_uuid] = vhd.cluster_id
        for vm_uuid, csv_names in csv_names_by_vm.items():
            if len(csv_names) < 2:
                continue
            seen_keys.add((AlertType.HYPERV_VM_MULTI_CSV, vm_uuid))
            vm_name = vm_name_by_uuid.get(vm_uuid, "?")
            sorted_csvs = sorted(csv_names)
            message = f"VM verteilt ihre Festplatten auf {len(sorted_csvs)} CSVs: {', '.join(sorted_csvs)}"
            existing = active_by_key.get((AlertType.HYPERV_VM_MULTI_CSV, vm_uuid))
            if existing is None:
                _trigger(
                    AlertType.HYPERV_VM_MULTI_CSV, vm_uuid,
                    object_name=vm_name,
                    hyperv_cluster_id=cluster_by_uuid.get(vm_uuid),
                    vm_name=vm_name,
                    message=message,
                )
            elif existing.message != message:
                existing.message = message

        # AVHDX ohne aktiven Checkpoint (Nutzer-Vorgabe 2026-09-11,
        # Backlog-Punkt 43): eine VM-Disk, deren discoverter Pfad auf
        # .avhdx endet, obwohl die VM laut Get-VMSnapshot KEINEN aktiven
        # Checkpoint hat, ist ein Zeichen fuer eingefrorene Discovery-Daten
        # -- eine volle Discovery hat den Zustand exakt waehrend eines
        # offenen Backup-Checkpoint-Fensters erfasst (structurelle
        # Kollision, siehe DISCOVERY_INTERVAL_ANCHOR oben) und seither ist
        # keine neue volle Discovery gelaufen. Live gefunden 2026-09-11:
        # RestoreTestVM_PG2/VM03 zeigten AVHDX ohne jeden Checkpoint, der
        # echte Knoten hatte laengst wieder die normale VHDX. Der
        # automatische Einzel-VM-Refresh nach jeder Backup-Checkpoint-
        # Entfernung (_execute_job_run, jobs.py) behebt die haeufige
        # Ursache meist sofort -- dieser Alarm ist das Sicherheitsnetz fuer
        # die restlichen Faelle (Refresh-Timeout, Altlast von vor diesem
        # Fix, oder ein wirklich haengender Merge) UND traegt die
        # "VM Discovery"-Beheben-Aktion (POST /api/vms/{cluster}/{vm}/
        # discover, siehe app.api.routes.vms).
        avhdx_grace_minutes = config.avhdx_without_checkpoint_grace_minutes if config else 30
        avhdx_cutoff = now - timedelta(minutes=avhdx_grace_minutes)
        avhdx_vhds_by_vm: dict[str, list[HyperVVhd]] = defaultdict(list)
        for vhd in db.query(HyperVVhd).filter(HyperVVhd.vm_uuid.isnot(None), HyperVVhd.path.isnot(None)).all():
            if vhd.path.lower().endswith(".avhdx"):
                avhdx_vhds_by_vm[vhd.vm_uuid].append(vhd)
        for vm in db.query(HyperVVm).filter(HyperVVm.vm_uuid.isnot(None)).all():
            vhds = avhdx_vhds_by_vm.get(vm.vm_uuid)
            if not vhds or vm.checkpoints:
                continue
            oldest_seen = min((v.last_seen_at for v in vhds if v.last_seen_at), default=None)
            if oldest_seen is None:
                continue
            if oldest_seen.tzinfo is None:
                oldest_seen = oldest_seen.replace(tzinfo=timezone.utc)
            if oldest_seen > avhdx_cutoff:
                continue  # noch innerhalb der Karenzzeit -- vermutlich der normale Refresh-Nachlauf
            key = vm.vm_uuid
            seen_keys.add((AlertType.HYPERV_VM_AVHDX_WITHOUT_CHECKPOINT, key))
            disk_names = ", ".join(sorted(v.path.rsplit("\\", 1)[-1] for v in vhds))
            message = (
                f"{len(vhds)} Festplatte(n) zeigen eine AVHDX-Differenzdatei, obwohl kein Checkpoint aktiv ist "
                f"(unveraendert seit {oldest_seen:%d.%m.%Y %H:%M} UTC): {disk_names}. Meist eingefrorene "
                "Discovery-Daten -- \"VM Discovery\" aktualisiert den Stand sofort."
            )
            existing = active_by_key.get((AlertType.HYPERV_VM_AVHDX_WITHOUT_CHECKPOINT, key))
            if existing is None:
                _trigger(
                    AlertType.HYPERV_VM_AVHDX_WITHOUT_CHECKPOINT, key,
                    object_name=vm.name,
                    hyperv_cluster_id=vm.cluster_id,
                    vm_name=vm.name,
                    message=message,
                )
            elif existing.message != message:
                existing.message = message

        for (alert_type, key), alert in active_by_key.items():
            if alert_type == AlertType.BACKUP_MISSED:
                continue  # loest sich nie automatisch -- siehe oben, nur manuell per dismiss
            if (alert_type, key) not in seen_keys:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = now
                _log(db, f"Warnung aufgeloest ({alert_type.value}): {alert.object_name}")

        db.commit()
    finally:
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
    Abbauen von LUN-Klon/iSCSI/Mount gepflegt werden muss.

    WICHTIG: die 24h gelten PRO SESSION ab deren eigenem started_at (siehe
    FileRestoreRun.expires_at, in der GUI als 'Automatisches Aufräumen' je
    Zeile sichtbar) -- NICHT ab dem Start dieses Tasks. Der stuendliche
    Poll ist nur die Umsetzung dieses per-Session-Zeitpunkts (kein
    eigener Timer-Thread pro Session, der einen Container-Neustart nicht
    ueberleben wuerde) -- eine Session, die z.B. um 14:23 geoeffnet wurde,
    wird beim naechsten Tick nach 14:23+24h aufgeraeumt, also mit bis zu
    ~1h Verzug gegenueber der exakten Minute, nicht 24h ab Task-Start.
    Bewusst KEIN 'Task gestartet'-Log hier (Nutzer-Rueckmeldung): stuendlich,
    aber praktisch immer ein No-Op -- die tatsaechliche Aufraeum-Aktion
    wird unten bereits gezielt geloggt."""
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
    ein fix bei start_scheduler registrierter CronTrigger).

    Bewusst KEIN 'Task gestartet'-Log hier (Nutzer-Rueckmeldung: alle 15min
    sichtbar, obwohl der Versand nur einmal taeglich um die konfigurierte
    Stunde stattfindet, war verwirrend) -- der tatsaechliche Versand wird
    unten stattdessen gezielt geloggt."""
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
        _log(db, f"E-Mail-Tageszusammenfassung fuer {today_label} versendet")

        if status_row is None:
            status_row = SchedulerStatus()
            db.add(status_row)
        status_row.last_email_summary_sent_date = today_label
        db.commit()
    finally:
        db.close()


# Obergrenze fuer den Nachhol-Mechanismus in run_scheduled_backups: eine
# groessere Luecke seit dem letzten Check (z.B. Container-Neustart/Deploy,
# oder der Prozess war laenger nicht lauffaehig) soll NICHT dazu fuehren,
# dass alle in der Zwischenzeit theoretisch faelligen Vorkommen auf einen
# Schlag nachgeholt werden -- das waere bei einer laengeren Downtime ein
# unkontrollierter Ausfuehrungs-Burst. Nur echte kurze Ueberlappungen
# (vorheriger Lauf hat wenige Minuten laenger als die eine Tick-Minute
# gebraucht) sollen abgedeckt werden.
SCHEDULE_CATCH_UP_MAX_MINUTES = 15


def run_scheduled_backups() -> None:
    """Fuehrt faellige Backup-Laeufe automatisch aus -- bislang gab es dafuer
    ueberhaupt keinen Trigger, Zeitplaene (Schedule) waren rein dekorativ und
    jeder Lauf musste manuell ueber 'Jetzt ausfuehren' angestossen werden.
    Laeuft minuetlich (siehe start_scheduler); die in Backup > Zeitplaene
    hinterlegten Uhrzeiten werden in der konfigurierten lokalen Zeitzone
    interpretiert (HVNB_SCHEDULE_TIMEZONE, Default Europe/Vienna), NICHT UTC
    -- der Container selbst laeuft komplett in UTC, ein Admin, der '08:30'
    eintraegt, meint aber die eigene Ortszeit.

    Der Zeitplan haengt an der Verknuepfung zwischen Resource Group und
    Policy (siehe app.models.resource_group.ResourceGroupPolicyLink), nicht
    an der Resource Group oder der Policy allein -- Nutzer-Ueberlegung: bei
    vielen Resource Groups (z.B. eine pro CSV, empfohlene Praxis), die sich
    dieselbe Policy teilen, wuerden sonst ALLE gleichzeitig gesichert (ein
    Policy-Zeitplan haette frueher alle verknuepften Resource Groups in
    einem gemeinsamen Lauf ausgeloest) -- Snapshot-/VSS-Lastspitze. Ausserdem
    kann dieselbe Resource Group an mehrere Policies mit unterschiedlicher
    Kadenz haengen (z.B. ein CSV stuendlich UND woechentlich, je eigene
    Policy) -- der Zeitplan pro Verknuepfung bildet das direkt ab. Iteriert
    daher ueber faellige Verknuepfungen, loest pro faelliger Verknuepfung
    einen eigenen, auf genau diese Resource Group beschraenkten Lauf aus
    (siehe resource_group_ids-Parameter von _start_job_run) -- unterschiedlich
    geplante Verknuepfungen laufen dadurch zeitversetzt statt zwangslaeufig
    gebuendelt.

    Ruft _start_job_run()/_execute_job_run() direkt auf (dieselben
    Funktionen, die der manuelle 'Jetzt ausfuehren'-Button verwendet), statt
    die Ausfuehrungslogik zu duplizieren -- dieser Aufruf blockiert bis der
    komplette Lauf (inkl. etwaiger Checkpoints) fertig ist, was fuer den
    eigenen Thread des BackgroundScheduler unproblematisch ist. max_instances=1
    (siehe start_scheduler) verhindert, dass ein noch laufender Lauf durch
    die naechste Minute ueberlappend erneut angestossen wird.

    WICHTIG (2026-09-02, echter Bug, live gefunden): frueher wurde pro Tick
    nur geprueft, ob ein Zeitplan GENAU zur aktuellen Minute faellig ist
    (strftime("%H:%M") in schedule.times). Ein einzelner geplanter Lauf
    dauert in der Praxis oft ueber 60 Sekunden (Snapshot/VSS/SnapMirror-
    Trigger) -- lief er in die naechste Minute hinein, wurde dieser Tick von
    APScheduler wegen max_instances=1 komplett uebersprungen ('skipped:
    maximum number of running instances reached'). Faellt in genau diese
    uebersprungene Minute zufaellig ein ANDERER Zeitplan (z.B. zwei
    Verknuepfungen nur 1-10 Minuten auseinander, wie vom Nutzer bereits
    manuell gestaffelt), wurde dessen Lauf dadurch nicht verspaetet, sondern
    ENDGUELTIG NIE gestartet -- ohne jede Fehlermeldung in der GUI. Bei
    aktuell wenigen, gut gestaffelten Zeitplaenen ist das noch nicht
    aufgetreten (Live-Pruefung: 0 aktuell haengende Laeufe, die einzigen drei
    'skipped'-Log-Zeilen waren jeweils genau die auf einen faelligen Lauf
    folgende Minute, in der kein zweiter Zeitplan lag), waere aber bei mehr
    Schedules (Nutzer-Szenario: 30 CSVs / 15 Zeitplaene) ein reales Risiko.
    Fix: statt der exakten aktuellen Minute wird das gesamte Intervall seit
    dem letzten erfolgreichen Check ausgewertet (SchedulerStatus.
    last_scheduled_backup_check_at, via _occurrences_within() -- dieselbe
    Funktion, die auch list_upcoming_jobs() fuer die Kalender-/Dashboard-
    Vorschau nutzt), begrenzt auf SCHEDULE_CATCH_UP_MAX_MINUTES nach hinten."""
    db = SessionLocal()
    try:
        settings = get_settings()
        try:
            tz = ZoneInfo(settings.schedule_timezone)
        except Exception:
            _log(db, f"Ungueltige HVNB_SCHEDULE_TIMEZONE '{settings.schedule_timezone}', falle auf UTC zurueck", level="WARNING")
            tz = timezone.utc
        now_local = datetime.now(tz)

        status_row = db.query(SchedulerStatus).first()
        if status_row is None:
            status_row = SchedulerStatus()
            db.add(status_row)
            db.flush()

        last_check_utc = status_row.last_scheduled_backup_check_at
        if last_check_utc is None:
            # Erster Lauf nach diesem Deploy (Spalte noch leer) -- nur die
            # aktuelle Minute pruefen statt rueckwirkend seit Ewigkeit
            # nachzuholen.
            last_check_local = now_local - timedelta(minutes=1)
        else:
            last_check_local = last_check_utc.astimezone(tz)
            max_lookback = now_local - timedelta(minutes=SCHEDULE_CATCH_UP_MAX_MINUTES)
            if last_check_local < max_lookback:
                _log(
                    db,
                    f"Geplante Backup-Pruefung war {now_local - last_check_local} nicht gelaufen (z.B. Neustart/Deploy) "
                    f"-- hole nur die letzten {SCHEDULE_CATCH_UP_MAX_MINUTES} Minuten nach, nicht die gesamte Luecke.",
                    level="WARNING",
                )
                last_check_local = max_lookback

        links = db.query(ResourceGroupPolicyLink).filter(ResourceGroupPolicyLink.schedule_id.isnot(None)).all()
        occurrences_by_schedule: dict[str, list[datetime]] = {}
        # Phase 1: NUR ermitteln, was in diesem Tick faellig ist -- noch
        # NICHTS ausfuehren. Siehe Begruendung unten, warum das strikt vor
        # jeder Ausfuehrung abgeschlossen und der Checkpoint committet sein
        # muss.
        due: list[tuple] = []
        for link in links:
            schedule = link.schedule
            policy = link.policy
            group = link.resource_group
            if schedule is None or policy is None or group is None or not schedule.times:
                continue
            if not policy.enabled:
                continue
            if schedule.id not in occurrences_by_schedule:
                occurrences_by_schedule[schedule.id] = _occurrences_within(schedule, last_check_local, now_local)
            for occurrence in occurrences_by_schedule[schedule.id]:
                due.append((link, schedule, policy, group, occurrence))

        # BUG (2026-09-02, per Nutzer-Screenshot aufgedeckt): der Checkpoint
        # wurde bisher erst NACH Ausfuehrung ALLER in diesem Tick faelligen
        # Verknuepfungen committet. Wurde der Prozess mitten in der
        # Ausfuehrung beendet (z.B. Deploy-Neustart), ging der GESAMTE
        # Fortschritt dieses Ticks verloren -- beim naechsten Start begann
        # das Nachhol-Fenster wieder VOR dem ersten faelligen Vorkommen
        # dieses Ticks. Fuer eine bereits erfolgreich abgeschlossene
        # Verknuepfung bedeutete das einen ECHTEN DOPPELTEN Lauf (live
        # beobachtet: Policy 'Silver_Daily'/Resource Group 'Silver_CSV01'
        # lief zweimal im Abstand von 2 Minuten); fuer eine gerade
        # unterbrochene Verknuepfung blieb die zugehoerige BackupRun-Zeile
        # zusaetzlich fuer immer auf 'running' stehen (siehe separater Fix
        # _reap_orphaned_in_progress_runs in init_db.py) und haette den
        # "laeuft bereits"-Schutz sonst dauerhaft blockiert.
        # Fix: den Checkpoint JETZT committen -- VOR jeder Ausfuehrung.
        # Ein Absturz waehrend der Ausfuehrung fuehrt dadurch bestenfalls
        # dazu, dass GENAU diese eine Ausfuehrung fehlschlaegt/haengen
        # bleibt (separat abgefangen), aber NIE zu einer erneuten
        # Ausfuehrung bereits committeter Vorkommen.
        status_row.last_scheduled_backup_check_at = datetime.now(timezone.utc)
        db.commit()

        # Phase 2: jetzt erst ausfuehren.
        for link, schedule, policy, group, occurrence in due:
            if group.paused:
                # Backlog-Punkt 36 (Nutzer-Vorgabe 2026-09-09): rein manuell
                # pausierte Protection Group -- faelliges Vorkommen bewusst
                # NICHT ausfuehren, aber auch nicht als Fehler loggen. Der
                # Checkpoint (status_row.last_scheduled_backup_check_at) ist
                # bereits VOR dieser Schleife committet, dieses Vorkommen wird
                # also nicht beim naechsten Tick nachgeholt. Die
                # backup_missed-Erkennung unten kennt paused_since/
                # paused_until und meldet es deshalb ebenfalls nicht als
                # verpasst.
                _log(
                    db,
                    f"Geplanter Backup-Lauf uebersprungen (Protection Group pausiert): '{group.name}' / "
                    f"Policy '{policy.name}' (Zeitplan '{schedule.name}', faellig {occurrence.strftime('%Y-%m-%d %H:%M')})",
                )
                continue
            try:
                # _execute_job_run laeuft hier bewusst synchron (nicht als
                # Hintergrund-Task wie beim manuellen "Jetzt ausfuehren",
                # siehe trigger_job_run in jobs.py) -- run_scheduled_backups
                # selbst laeuft ja bereits im eigenen APScheduler-
                # Hintergrund-Thread, und max_instances=1 auf diesem Job
                # (siehe start_scheduler) verhindert echte Ueberlappungen;
                # der Nachhol-Mechanismus oben faengt dafuer verpasste
                # Minuten ab.
                _log(
                    db,
                    f"Geplanter Backup-Lauf gestartet: Resource Group '{group.name}' / Policy '{policy.name}' "
                    f"(Zeitplan '{schedule.name}', faellig {occurrence.strftime('%Y-%m-%d %H:%M')})",
                )
                run, warnings = _start_job_run(policy, db, resource_group_ids={group.id})
                _execute_job_run(run.id, warnings)
            except Exception as exc:
                _log(
                    db,
                    f"Geplanter Backup-Lauf fuer Resource Group '{group.name}' / Policy '{policy.name}' fehlgeschlagen: {exc}",
                    level="ERROR",
                )
    finally:
        db.close()


def force_cancel_timed_out_runs() -> None:
    """Zeitlimit-Watchdog fuer Backup-Laeufe. Deckt zwei Faelle ab, beide
    GUI-konfigurierbar (Settings > Hintergrundjobs, scheduler_config):

    1. Abbruch angefordert, reagiert aber nicht
       (backup_cancel_force_timeout_minutes, Default 10). POST
       /jobs/runs/{id}/cancel setzt nur cancel_requested_at;
       _execute_job_run (app.api.routes.jobs) prueft das Feld
       ausschliesslich ZWISCHEN den Schritten. Haengt ein einzelner
       WinRM-/NetApp-Aufruf laenger als sein eigenes Timeout (oder
       ueberhaupt -- z.B. eine TCP-Verbindung, die weder ankommt noch
       abgewiesen wird), bliebe der Lauf sonst beliebig lange als 'laeuft /
       Abbruch angefordert' stehen.

    2. Gesamtlaufzeit ueberschritten (backup_run_max_duration_minutes,
       Default 0 = aus). Harte Obergrenze fuer JEDEN laufenden Lauf, auch
       ohne Abbruch-Anforderung -- fuer den Fall, dass ein Lauf haengt,
       ohne dass jemand ihn manuell abbricht.

    In beiden Faellen blockiert die verwaiste 'laeuft'-Zeile sonst ueber
    den "laeuft bereits"-Schutz (_start_job_run) dauerhaft jeden kuenftigen
    Lauf derselben Resource Group + Policy.

    Laeuft minuetlich als EIGENER APScheduler-Job -- bewusst nicht als Teil
    von run_scheduled_backups, dessen synchroner _execute_job_run-Aufruf ja
    selbst der Haenger sein kann (max_instances=1 wuerde diesen Watchdog
    dann mitblockieren). Der ggf. noch haengende Worker-Thread erkennt beim
    spaeteren Zuruecklaufen an finished_at != None, dass er sein Ergebnis
    verwerfen muss, statt den erzwungenen Abbruch zu ueberschreiben (siehe
    _execute_job_run).

    Etwaige zu diesem Zeitpunkt auf einem Hyper-V-Knoten noch offene
    Applikationskonsistenz-Checkpoints kann dieser DB-only-Job NICHT
    aufraeumen (der haengende Thread haelt die einzige WinRM-Session, ein
    Neustart hat sie ganz verloren) -- sie fallen in die separate
    Verwaiste-Checkpoint-Erkennung (run_alert_check) inkl. Loesch-Aktion in
    Inventar/Alarmen."""
    db = SessionLocal()
    try:
        config = db.query(SchedulerConfig).first()
        cancel_timeout = config.backup_cancel_force_timeout_minutes if config else 10
        max_duration = config.backup_run_max_duration_minutes if config else 0
        now = datetime.now(timezone.utc)

        # run_id -> (BackupRun, Begruendungstext). setdefault(): steht ein
        # Lauf wegen beider Kriterien an, gewinnt der Abbruch-Grund (zuerst
        # eingetragen) -- er ist die konkretere Erklaerung.
        stuck: dict[str, tuple[BackupRun, str]] = {}

        if cancel_timeout and cancel_timeout > 0:
            for run in (
                db.query(BackupRun)
                .filter(
                    BackupRun.status == JobStatus.RUNNING,
                    BackupRun.cancel_requested_at.isnot(None),
                    BackupRun.cancel_requested_at < now - timedelta(minutes=cancel_timeout),
                )
                .all()
            ):
                stuck[run.id] = (
                    run,
                    f"Abbruch nach Zeitlimit erzwungen ({cancel_timeout} min nach der Abbruch-Anforderung "
                    "noch immer aktiv -- ein Schritt reagierte nicht)",
                )

        if max_duration and max_duration > 0:
            for run in (
                db.query(BackupRun)
                .filter(
                    BackupRun.status == JobStatus.RUNNING,
                    BackupRun.started_at < now - timedelta(minutes=max_duration),
                )
                .all()
            ):
                stuck.setdefault(
                    run.id,
                    (run, f"Hart abgebrochen: Gesamtlaufzeit-Limit von {max_duration} min ueberschritten"),
                )

        for run, note in stuck.values():
            run.status = JobStatus.CANCELLED
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = f"{note}; {run.error_message}" if run.error_message else note
            db.add(
                BackupRunStep(
                    run_id=run.id, step="run-finished",
                    label="Backup abgebrochen (Zeitlimit erzwungen)",
                    message=note, status=RestoreStepStatus.SKIPPED,
                )
            )
            _log(
                db,
                f"Backup-Lauf '{run.policy_name}' (Lauf {run.id}) hart abgeschlossen: {note}. "
                "Etwaige offene Hyper-V-Checkpoints raeumt die Verwaiste-Checkpoint-Erkennung auf.",
                level="WARNING",
            )
        if stuck:
            db.commit()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    settings = get_settings()

    # Zeitplaene der vier folgenden Jobs sind GUI-konfigurierbar (Settings >
    # Hintergrundjobs, siehe app.api.routes.scheduler_config) -- die
    # SchedulerConfig-Singleton-Zeile existiert bereits durch init_db()
    # (Startwerte aus den bisherigen ENV-Variablen), env-Werte hier nur als
    # Sicherheitsnetz falls init_db() aus irgendeinem Grund noch nicht
    # gelaufen ist. Eine spaetere Aenderung ueber die GUI ruft
    # scheduler.reschedule_job() live auf denselben Job-IDs auf, statt den
    # Container neu zu starten.
    startup_db = SessionLocal()
    try:
        config = startup_db.query(SchedulerConfig).first()
        alert_config = startup_db.query(AlertConfig).first()
    finally:
        startup_db.close()
    hc_interval = config.healthcheck_interval_minutes if config else settings.healthcheck_interval_minutes
    discovery_interval = config.discovery_interval_minutes if config else settings.discovery_interval_minutes
    snapshot_hour = config.snapshot_reconcile_hour if config else settings.snapshot_reconcile_hour
    retention_hour = config.retention_cleanup_hour if config else settings.snapshot_reconcile_hour
    alert_check_interval = alert_config.alert_check_interval_minutes if alert_config else 5

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_health_checks, IntervalTrigger(minutes=hc_interval, start_date=INTERVAL_ANCHOR),
        id="health-check", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_discovery, IntervalTrigger(minutes=discovery_interval, start_date=DISCOVERY_INTERVAL_ANCHOR),
        id="discovery", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_snapshot_reconciliation, CronTrigger(hour=snapshot_hour, minute=0),
        id="snapshot-reconciliation", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_retention_cleanup, CronTrigger(hour=retention_hour, minute=15),
        id="retention-cleanup", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_scheduled_backups, CronTrigger(minute="*"),
        id="scheduled-backups", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        force_cancel_timed_out_runs, CronTrigger(minute="*"),
        id="force-cancel-timed-out-runs", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_file_restore_expiry, IntervalTrigger(hours=1, start_date=INTERVAL_ANCHOR),
        id="file-restore-expiry", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_daily_email_summary, IntervalTrigger(minutes=15, start_date=INTERVAL_ANCHOR),
        id="daily-email-summary", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_alert_check, IntervalTrigger(minutes=alert_check_interval, start_date=INTERVAL_ANCHOR),
        id="alert-check", replace_existing=True, max_instances=1,
    )
    scheduler.start()
    startup_db = SessionLocal()
    try:
        _log(
            startup_db,
            # "Scheduler-Prozess gestartet" statt nur "gestartet" -- neben
            # den einzelnen "Task gestartet: X"-Zeilen pro Hintergrundjob
            # (siehe run_health_checks etc.) las sich das sonst wie ein
            # weiterer Task-Start, ist aber eine EINMALIGE Zusammenfassung
            # beim Hochfahren des Prozesses (jeder Deploy-Neustart), die
            # ALLE konfigurierten Intervalle auflistet -- nicht nur den
            # Health-Check (Nutzer-Rueckfrage: "sollte hier nicht nur
            # Health-Check erscheinen?" -- nein, das war schon immer
            # beabsichtigt, nur die Formulierung war missverstaendlich).
            f"Scheduler-Prozess gestartet (Konfiguration: Health-Check alle {hc_interval}min, "
            f"Discovery alle {discovery_interval}min, "
            f"Snapshot-Abgleich taeglich um {snapshot_hour:02d}:00 UTC, "
            f"Retention-Cleanup taeglich um {retention_hour:02d}:15 UTC, "
            f"geplante Backups minuetlich geprueft in Zeitzone {settings.schedule_timezone}, "
            "haengende abgebrochene Backup-Laeufe minuetlich per Zeitlimit-Watchdog beendet, "
            f"Datei-Restore-Sicherheitsnetz stuendlich (Zeitlimit {settings.file_restore_max_age_hours}h), "
            f"E-Mail-Tageszusammenfassung alle 15min geprueft, "
            f"Warnungs-Check (Kapazitaet/Cluster/SnapMirror) alle {alert_check_interval}min)",
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


def get_scheduler() -> BackgroundScheduler | None:
    """Zugriff auf die laufende Scheduler-Instanz fuer app.api.routes.
    scheduler_config, um bei einer Config-Aenderung ueber die GUI
    scheduler.reschedule_job() live aufzurufen -- ohne Container-Neustart."""
    return _scheduler
