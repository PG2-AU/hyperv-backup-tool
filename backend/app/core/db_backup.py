"""Automatische Sicherung der App-Datenbank + Wiederherstellung (Backlog #66,
umgesetzt 2026-09-28).

Nutzer-Vorgaben:
- Taeglich auf eine CIFS-Freigabe (smbprotocol, siehe app.services.
  smb_target), Aufbewahrung X Tage.
- Zusaetzlich die letzten N (Standard 3) Kopien lokal im /data-Volume, damit
  auch bei nicht erreichbarer Freigabe eine aktuelle Sicherung existiert.
- Wiederherstellen auch per GUI.
- Keine eigene Passphrase: die Kennwoerter in der DB sind ohnehin mit
  HVNB_SECRET_KEY verschluesselt, der Rest wird ueber die Berechtigungen der
  Freigabe geschuetzt. HVNB_SECRET_KEY wird bewusst NICHT mitgesichert --
  sonst waere diese Verschluesselung wertlos. Eine Wiederherstellung ist
  deshalb nur mit identischem Schluessel sinnvoll; inspect_backup prueft
  das vorab und sperrt sonst.

Konsistente Kopie im laufenden Betrieb ueber die SQLite-Online-Backup-API
(sqlite3.Connection.backup) -- sicher auch waehrend Backups schreiben.
Wiederherstellen nutzt dieselbe API in Gegenrichtung (Sicherung -> Live-
DB), danach startet die App neu (supervisord startet uvicorn automatisch
wieder), damit init_db die Migrationen anwendet und kein Prozesszustand
(Scheduler-Intervalle, Caches) aus der alten DB uebrig bleibt."""

import gzip
import os
import re
import signal
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.models.backup_run import BackupRun, JobStatus
from app.models.csv_resize_run import CsvResizeRun
from app.models.db_backup import DbBackupConfig
from app.models.file_restore_run import FileRestoreRun
from app.models.restore_run import RestoreRun, RestoreStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_move_run import VmMoveRun
from app.models.vm_recreate_run import VmRecreateRun
from app.services.smb_target import SmbTarget, SmbTargetError

# Nur Dateien nach genau diesem Muster werden aufgelistet, wiederhergestellt
# und von der Aufbewahrung geloescht -- fremde Dateien im Zielordner bleiben
# unangetastet.
_FILE_RE = re.compile(r"^hvnb-db-(?P<host>[A-Za-z0-9-]+)-(?P<ts>\d{8}-\d{6})(?P<pre>-vor-restore)?\.sqlite\.gz$")
_TS_FORMAT = "%Y%m%d-%H%M%S"
# Wie viele automatische Kopien "vor der Wiederherstellung" lokal bleiben.
_PRE_RESTORE_KEEP = 3


class DbBackupError(RuntimeError):
    pass


# --- Pfade / Namen ---------------------------------------------------------------


def db_file_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise DbBackupError("DB-Sicherung unterstuetzt nur SQLite.")
    return Path(url[len("sqlite:///"):]).resolve()


def local_backup_dir() -> Path:
    path = db_file_path().parent / "db-backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Kennung im Dateinamen -- bewusst NICHT der Hostname: im Container ist das
# die Container-ID, die sich bei jedem Neuerstellen (Quadlet-Neustart)
# aendert; die Aufbewahrung haette aeltere Sicherungen dann als "fremd"
# betrachtet und nie mehr geloescht (live gefunden 2026-09-28).
INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")


def instance_name(config: DbBackupConfig | None) -> str:
    name = (config.instance_name if config is not None else "") or ""
    return name if INSTANCE_NAME_RE.match(name) else "hvnb"


def _file_name(name: str, pre_restore: bool = False) -> str:
    return f"hvnb-db-{name}-{datetime.now(timezone.utc).strftime(_TS_FORMAT)}{'-vor-restore' if pre_restore else ''}.sqlite.gz"


@dataclass
class BackupFileInfo:
    name: str
    size_bytes: int
    created_at: datetime
    host: str
    pre_restore: bool


def _parse(name: str, size: int) -> BackupFileInfo | None:
    match = _FILE_RE.match(name)
    if not match:
        return None
    created = datetime.strptime(match.group("ts"), _TS_FORMAT).replace(tzinfo=timezone.utc)
    return BackupFileInfo(name=name, size_bytes=size, created_at=created, host=match.group("host"), pre_restore=bool(match.group("pre")))


# --- Sicherung -------------------------------------------------------------------


def _snapshot_bytes() -> tuple[bytes, int]:
    """Konsistente, gzip-komprimierte Kopie der Live-DB. (Bytes, Rohgroesse)"""
    source_path = db_file_path()
    with tempfile.NamedTemporaryFile(dir=local_backup_dir(), suffix=".sqlite", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        source = sqlite3.connect(str(source_path), timeout=30)
        target = sqlite3.connect(str(tmp_path))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        raw = tmp_path.read_bytes()
        return gzip.compress(raw, compresslevel=6), len(raw)
    finally:
        tmp_path.unlink(missing_ok=True)


def _rotate_local(keep: int, pre_restore: bool) -> None:
    files = sorted(
        (info for p in local_backup_dir().iterdir() if (info := _parse(p.name, 0)) and info.pre_restore == pre_restore),
        key=lambda i: i.created_at, reverse=True,
    )
    for info in files[max(keep, 0):]:
        (local_backup_dir() / info.name).unlink(missing_ok=True)


def save_local_copy(instance: str, pre_restore: bool = False, keep: int = 3) -> tuple[str, int]:
    data, _ = _snapshot_bytes()
    name = _file_name(instance, pre_restore)
    (local_backup_dir() / name).write_bytes(data)
    _rotate_local(_PRE_RESTORE_KEEP if pre_restore else keep, pre_restore)
    return name, len(data)


def _target(config: DbBackupConfig) -> SmbTarget:
    try:
        password = decrypt_secret(config.encrypted_password) if config.encrypted_password else ""
    except InvalidToken as exc:
        raise SmbTargetError(
            "Das gespeicherte Kennwort der Freigabe laesst sich nicht entschluesseln (HVNB_SECRET_KEY geaendert?) -- "
            "bitte unter Settings > DB-Sicherung neu eintragen."
        ) from exc
    return SmbTarget(config.share_path, config.username, password)


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="db-backup", message=message))
    db.commit()


def run_db_backup(db: Session, *, triggered_by: str) -> DbBackupConfig:
    """Eine Sicherung: lokale Kopie (+ Rotation), Upload auf die Freigabe,
    Aufbewahrung dort. Status landet in der DbBackupConfig-Zeile. Wirft
    nicht -- Fehler stehen in config.last_error (fuer Alarm + GUI)."""
    config = db.query(DbBackupConfig).first()
    if config is None or not config.share_path:
        raise DbBackupError("DB-Sicherung ist nicht eingerichtet.")
    now = datetime.now(timezone.utc)
    config.last_attempt_at = now
    config.last_upload_failed = False
    try:
        data, raw_size = _snapshot_bytes()
        name = _file_name(instance_name(config))
        (local_backup_dir() / name).write_bytes(data)
        _rotate_local(config.local_keep, pre_restore=False)
    except Exception as exc:  # noqa: BLE001
        config.last_error = f"Lokale Kopie fehlgeschlagen: {exc}"[:2000]
        db.commit()
        _log(db, f"DB-Sicherung fehlgeschlagen ({triggered_by}): {config.last_error}", level="ERROR")
        return config

    try:
        deleted = 0
        with _target(config) as target:
            target.write(name, data)
            cutoff = now - timedelta(days=config.retention_days)
            for entry in target.list_files():
                info = _parse(entry.name, entry.size_bytes)
                if info and not info.pre_restore and info.host == instance_name(config) and info.created_at < cutoff:
                    target.remove(entry.name)
                    deleted += 1
    except Exception as exc:  # noqa: BLE001
        detail = str(exc) or type(exc).__name__
        config.last_error = f"Lokal gesichert ({name}), aber Upload auf die Freigabe fehlgeschlagen: {detail}"[:2000]
        config.last_upload_failed = True
        db.commit()
        _log(db, f"DB-Sicherung teilweise fehlgeschlagen ({triggered_by}): {config.last_error}", level="ERROR")
        return config

    config.last_success_at = now
    config.last_error = None
    config.last_file_name = name
    config.last_size_bytes = len(data)
    db.commit()
    _log(
        db,
        f"DB-Sicherung erstellt ({triggered_by}): {name}, {len(data) // 1024} KB komprimiert ({raw_size // 1024} KB roh)"
        + (f", {deleted} alte Sicherung(en) auf der Freigabe entfernt" if deleted else ""),
    )
    return config


def test_target(config: DbBackupConfig) -> str:
    name = f"hvnb-verbindungstest-{instance_name(config)}.tmp"
    with _target(config) as target:
        target.write(name, b"hvnb connection test")
        target.remove(name)
        count = sum(1 for f in target.list_files() if _parse(f.name, f.size_bytes))
    return f"Schreiben und Loeschen auf {target.root} erfolgreich ({count} vorhandene Sicherung(en))."


@dataclass
class BackupListing:
    share: list[BackupFileInfo] = field(default_factory=list)
    local: list[BackupFileInfo] = field(default_factory=list)
    share_error: str | None = None


def list_backups(db: Session) -> BackupListing:
    listing = BackupListing()
    for path in local_backup_dir().iterdir():
        info = _parse(path.name, path.stat().st_size)
        if info:
            listing.local.append(info)
    config = db.query(DbBackupConfig).first()
    if config is not None and config.share_path:
        try:
            with _target(config) as target:
                listing.share = [i for f in target.list_files() if (i := _parse(f.name, f.size_bytes))]
        except Exception as exc:  # noqa: BLE001
            listing.share_error = str(exc)
    listing.local.sort(key=lambda i: i.created_at, reverse=True)
    listing.share.sort(key=lambda i: i.created_at, reverse=True)
    return listing


# --- Wiederherstellung -----------------------------------------------------------


def fetch_backup(db: Session, source: str, name: str) -> Path:
    """Entpackt die gewaehlte Sicherung in eine temporaere SQLite-Datei."""
    if not _FILE_RE.match(name):
        raise DbBackupError("Unbekannter Dateiname.")
    if source == "local":
        path = local_backup_dir() / name
        if not path.exists():
            raise DbBackupError(f"Lokale Sicherung {name} nicht gefunden.")
        data = path.read_bytes()
    elif source == "share":
        config = db.query(DbBackupConfig).first()
        if config is None or not config.share_path:
            raise DbBackupError("Keine Freigabe eingerichtet.")
        with _target(config) as target:
            data = target.read(name)
    else:
        raise DbBackupError("Quelle muss 'local' oder 'share' sein.")
    return unpack_to_temp(data)


def unpack_to_temp(data: bytes) -> Path:
    try:
        raw = gzip.decompress(data)
    except OSError as exc:
        raise DbBackupError("Datei ist keine gueltige, gzip-komprimierte Sicherung.") from exc
    with tempfile.NamedTemporaryFile(dir=local_backup_dir(), suffix=".restore.sqlite", delete=False) as tmp:
        tmp.write(raw)
        return Path(tmp.name)


@dataclass
class BackupInspection:
    counts: dict[str, int] = field(default_factory=dict)
    latest_backup_run_at: str | None = None
    secret_key_ok: bool | None = None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_COUNT_TABLES = {
    "hyperv_clusters": "Hyper-V-Cluster",
    "netapp_clusters": "NetApp-Systeme",
    "backup_policies": "Backup-Policies",
    "resource_groups": "Protection Groups",
    "backup_runs": "Backup-Läufe",
    "users": "Benutzer",
}
_SECRET_COLUMNS = (
    ("hyperv_clusters", "encrypted_password"),
    ("netapp_clusters", "encrypted_password"),
    ("restore_proxy_host", "encrypted_password"),
    ("email_config", "encrypted_password"),
    ("ad_config", "encrypted_bind_password"),
)


def inspect_backup(path: Path) -> BackupInspection:
    result = BackupInspection()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        result.blockers.append(f"Datei ist keine lesbare SQLite-Datenbank: {exc}")
        return result
    try:
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            result.blockers.append(f"Datei ist keine SQLite-Datenbank: {exc}")
            return result
        if integrity != "ok":
            result.blockers.append(f"Integritaetspruefung fehlgeschlagen: {integrity}")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in ("users", "roles", "hyperv_clusters", "backup_policies") if t not in tables]
        if missing:
            result.blockers.append(f"Keine Datenbank dieser Anwendung (Tabellen fehlen: {', '.join(missing)}).")
            return result
        for table, label in _COUNT_TABLES.items():
            if table in tables:
                result.counts[label] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if "backup_runs" in tables:
            latest = conn.execute("SELECT MAX(started_at) FROM backup_runs").fetchone()[0]
            result.latest_backup_run_at = str(latest) if latest else None

        # Stimmt HVNB_SECRET_KEY? Ein beliebiges gespeichertes Kennwort
        # probeweise entschluesseln -- sonst waeren nach der Wiederherstellung
        # alle Zugangsdaten unbrauchbar.
        for table, column in _SECRET_COLUMNS:
            if table not in tables:
                continue
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                continue
            row = conn.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != '' LIMIT 1").fetchone()
            if row is None:
                continue
            try:
                decrypt_secret(row[0])
                result.secret_key_ok = True
            except Exception:  # noqa: BLE001
                result.secret_key_ok = False
                result.blockers.append(
                    "Die gespeicherten Kennwörter lassen sich mit dem HVNB_SECRET_KEY dieser Installation nicht "
                    "entschlüsseln -- die Sicherung stammt von einer Installation mit anderem Schlüssel. Zuerst den "
                    "ursprünglichen HVNB_SECRET_KEY in die .env eintragen und den Container neu starten."
                )
            break
    finally:
        conn.close()
    return result


def running_jobs(db: Session) -> list[str]:
    """Laufende Vorgaenge, die eine Wiederherstellung verhindern -- ihre
    Zeilen und ggf. temporaeren Ressourcen (LUN-Klone, iSCSI-Sitzungen)
    wuerden sonst in der wiederhergestellten DB fehlen."""
    reasons = []
    backups = db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CLEANING_UP])).count()
    if backups:
        reasons.append(f"{backups} laufende(r) Backup-Lauf/Läufe")
    for model, label in (
        (RestoreRun, "Restore"), (VmRecreateRun, "VM-Neuerstellung"), (FileRestoreRun, "offene Datei-Restore-Sitzung"),
        (VmMoveRun, "VM-Verschiebung"), (CsvResizeRun, "CSV-Vergrößerung"),
    ):
        count = db.query(model).filter(model.status == RestoreStatus.RUNNING).count()
        if count:
            reasons.append(f"{count} {label}")
    return reasons


def restore_database(db: Session, restore_path: Path, *, actor: str, source_label: str) -> str:
    """Ersetzt die Live-DB durch die (bereits geprueften) Sicherung und
    startet die App neu. Legt vorher lokal eine Sicherheitskopie des
    aktuellen Stands an (Muster *-vor-restore*). Liefert deren Namen."""
    from app.core.scheduler import get_scheduler
    from app.db.session import engine

    blockers = running_jobs(db)
    if blockers:
        raise DbBackupError("Wiederherstellung nicht moeglich, es laeuft noch: " + ", ".join(blockers))

    safety_name, _ = save_local_copy(instance_name(db.query(DbBackupConfig).first()), pre_restore=True)
    scheduler = get_scheduler()
    if scheduler is not None:
        scheduler.pause()
    db.close()
    engine.dispose()

    source = sqlite3.connect(str(restore_path))
    target = sqlite3.connect(str(db_file_path()), timeout=60)
    try:
        source.backup(target)
        target.execute(
            "INSERT INTO system_log_events (id, level, source, message, timestamp) VALUES (?, 'WARNING', 'db-backup', ?, ?)",
            (
                str(uuid.uuid4()),
                f"Datenbank wiederhergestellt aus {source_label} (durch {actor}); Stand davor gesichert als {safety_name}",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
            ),
        )
        target.commit()
    finally:
        target.close()
        source.close()
        restore_path.unlink(missing_ok=True)

    _schedule_restart()
    return safety_name


def _schedule_restart(delay_sec: float = 1.5) -> None:
    """Beendet den uvicorn-Prozess kurz nach der HTTP-Antwort --
    supervisord (autorestart=true) startet ihn neu, init_db laeuft dabei auf
    der wiederhergestellten DB (Migrationen, Aufraeumen verwaister Laeufe)."""
    def _stop() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        threading.Timer(15, lambda: os._exit(0)).start()

    threading.Timer(delay_sec, _stop).start()


def cleanup_temp_files() -> None:
    for path in local_backup_dir().glob("*.restore.sqlite"):
        path.unlink(missing_ok=True)

