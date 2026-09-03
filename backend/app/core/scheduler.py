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
from app.models.backup_policy import BackupPolicy, RetentionType
from app.models.backup_run import BackupRun, BackupRunSnapshot, BackupRunSnapshotDestination, JobStatus
from app.models.email_config import EmailConfig
from app.models.file_restore_run import FileRestoreRun
from app.models.hyperv_cluster import HyperVCluster, HyperVClusterHealth
from app.models.hyperv_discovery import HyperVCsv
from app.models.netapp_cluster import NetAppCluster, NetAppClusterHealth
from app.models.netapp_discovery import NetAppLun, NetAppSnapMirrorRelationship, NetAppVolume
from app.models.resource_group import ResourceGroupPolicyLink
from app.models.restore_run import RestoreRun, RestoreStatus
from app.models.scheduler_config import SchedulerConfig
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


def run_discovery() -> None:
    db = SessionLocal()
    try:
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
    Teil dieses Checks, siehe app.models.alert."""
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
    finally:
        startup_db.close()
    hc_interval = config.healthcheck_interval_minutes if config else settings.healthcheck_interval_minutes
    discovery_interval = config.discovery_interval_minutes if config else settings.discovery_interval_minutes
    snapshot_hour = config.snapshot_reconcile_hour if config else settings.snapshot_reconcile_hour
    retention_hour = config.retention_cleanup_hour if config else settings.snapshot_reconcile_hour

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_health_checks, IntervalTrigger(minutes=hc_interval),
        id="health-check", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_discovery, IntervalTrigger(minutes=discovery_interval),
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
        run_file_restore_expiry, IntervalTrigger(hours=1),
        id="file-restore-expiry", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_daily_email_summary, IntervalTrigger(minutes=15),
        id="daily-email-summary", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        run_alert_check, IntervalTrigger(minutes=15),
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
            f"Datei-Restore-Sicherheitsnetz stuendlich (Zeitlimit {settings.file_restore_max_age_hours}h), "
            f"E-Mail-Tageszusammenfassung alle 15min geprueft, "
            f"Warnungs-Check (Kapazitaet/Cluster/SnapMirror) alle 15min)",
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
