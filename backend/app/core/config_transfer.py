"""Konfigurations-Export/-Import (Backlog #45, umgesetzt 2026-09-28).

Zweck: die komplette *Einrichtung* dieser Installation (Cluster-Registrie-
rungen, Policies, Protection Groups, Zeitplaene, Standorte, alle Settings,
Benutzer/Rollen) als ZIP sichern und in eine frische Installation
uebertragen -- z.B. neuer Server nach Ausfall oder Umzug.

Nutzer-Entscheidungen 2026-09-28:
- **Keine Kennwoerter im Export** (auch keine NetApp-Client-Zertifikate und
  keine Passwort-Hashes lokaler Benutzer). Nach dem Import traegt der Admin
  sie einmal ueber die vorhandenen Bearbeiten-Dialoge neu ein. Umgeht
  damit komplett das HVNB_SECRET_KEY-Problem (der Schluessel steckt in der
  .env und ist auf dem neuen Server ein anderer). Passphrase-Variante:
  Backlog #65.
- **Import nur in eine leere Installation** (keine Cluster/Systeme/
  Policies/Protection Groups/Zeitplaene vorhanden) -- kein Zusammenfuehren.
- **Backup-Katalog optional** (abgeschlossene Backup-Laeufe mit Snapshots,
  SnapMirror-Zielen und VM-Konfigurationen): ohne ihn kennt ein neuer Server
  die weiterhin auf der NetApp liegenden Snapshots nicht als
  Wiederherstellungspunkte.

Format: ZIP mit manifest.json und config/<tabelle>.json. Die Zeilen werden
ROH aus SQLite gelesen und roh zurueckgeschrieben (SELECT * / INSERT per
text()), damit Enum-Namen, JSON-Text und das eigene DateTime-Format exakt
erhalten bleiben, ohne jede Spalte einzeln durch die ORM-Typen zu schicken.
Interne IDs bleiben identisch -- Protection-Group-Mitglieder und Standort-
Zuordnungen verweisen ueber die Cluster-ID (siehe make_member_key), neue
IDs wuerden sie verwaisen lassen.

Discovery-Daten, Alarme, System Log, Kapazitaetsverlauf und Restore-/
Move-Laeufe werden bewusst NICHT exportiert: Discovery holt sich der neue
Server selbst, der Rest ist Historie dieses Servers."""

import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

# Formatversion des Exports -- erhoehen, wenn sich die Bedeutung bestehender
# Felder aendert (neue Tabellen/Spalten allein brauchen das nicht: fehlende
# Spalten bekommen beim Import ihren Standardwert, siehe import_config).
FORMAT_VERSION = 1


@dataclass(frozen=True)
class _TableSpec:
    name: str
    label: str
    # Spalten, die NIE exportiert werden (Kennwoerter, Zertifikatspfade).
    scrub: tuple[str, ...] = ()
    # Laufzeit-/Statusspalten, die im Export auf einen neutralen Wert
    # gesetzt werden (gehoeren zum Zustand DIESES Servers).
    reset: tuple[tuple[str, object], ...] = ()


CONFIG_TABLES: tuple[_TableSpec, ...] = (
    _TableSpec(
        "hyperv_clusters", "Hyper-V-Cluster", scrub=("encrypted_password",),
        reset=(("health", "UNKNOWN"), ("last_checked_at", None), ("last_check_error", None), ("unreachable_nodes_json", None)),
    ),
    _TableSpec(
        "netapp_clusters", "NetApp-Systeme", scrub=("encrypted_password", "client_cert_path", "client_key_path"),
        reset=(("health", "UNKNOWN"), ("last_checked_at", None), ("last_check_error", None), ("metrocluster_mode", None)),
    ),
    _TableSpec("restore_proxy_host", "Restore-Proxy-Host", scrub=("encrypted_password",)),
    _TableSpec("restore_infra_configs", "Restore-Infrastruktur"),
    _TableSpec("snapmirror_labels", "SnapMirror-Labels"),
    _TableSpec("schedules", "Zeitpläne"),
    _TableSpec("backup_policies", "Backup-Policies"),
    _TableSpec("resource_groups", "Protection Groups"),
    _TableSpec("resource_group_policies", "Protection-Group-Verknüpfungen"),
    _TableSpec("allowed_schedule_collisions", "Erlaubte Zeitplan-Kollisionen"),
    # Schutzklassen (Backlog #86) samt Zuordnung an VMs/CSVs/Freigaben -- die
    # Zuordnung haengt an hyperv_clusters.id, die der Export beibehaelt.
    _TableSpec("protection_classes", "Schutzklassen"),
    _TableSpec("protection_class_assignments", "Schutzklassen-Zuordnungen"),
    # Report-Vorlagen (Backlog #84) mit Zeitplan und Empfaengern; die erzeugten
    # Reports (Historie, PDF-Dateien) gehoeren zum alten Server und bleiben dort.
    _TableSpec("report_definitions", "Report-Vorlagen"),
    _TableSpec("sites", "Standorte"),
    _TableSpec("hyperv_node_sites", "Standort-Zuordnungen Hyper-V-Knoten"),
    _TableSpec("csv_site_overrides", "Standort-Zuordnungen CSVs"),
    _TableSpec("alert_config", "Alarm-Einstellungen"),
    _TableSpec("scheduler_config", "Hintergrundjob-Einstellungen"),
    _TableSpec("storage_access_config", "Storage-Zugriff"),
    _TableSpec("email_config", "E-Mail-Einstellungen", scrub=("encrypted_password",)),
    _TableSpec("kerberos_config", "Kerberos-Einstellungen"),
    _TableSpec("ad_config", "Active-Directory-Einstellungen", scrub=("encrypted_bind_password",)),
    _TableSpec("winrm_host_certificates", "WinRM-Zertifikate"),
    _TableSpec(
        "db_backup_config", "DB-Sicherung", scrub=("encrypted_password",),
        reset=(
            ("last_attempt_at", None), ("last_success_at", None), ("last_file_name", None), ("last_size_bytes", None),
            ("last_error", None), ("last_upload_failed", 0), ("enabled_since", None),
        ),
    ),
    _TableSpec(
        "winrm_trust_state", "WinRM-Zertifikatsbundle",
        reset=(("bundle_path", None), ("last_bundle_built_at", None), ("bundle_cert_count", 0)),
    ),
)

USER_TABLES: tuple[_TableSpec, ...] = (
    _TableSpec("roles", "Rollen"),
    _TableSpec("users", "Benutzer", scrub=("hashed_password",), reset=(("last_login_at", None),)),
    _TableSpec("role_assignments", "Rollenzuweisungen"),
)

CATALOG_TABLES: tuple[_TableSpec, ...] = (
    _TableSpec("backup_runs", "Backup-Läufe"),
    _TableSpec("backup_run_snapshots", "Backup-Snapshots"),
    _TableSpec("backup_run_snapshot_destinations", "SnapMirror-Ziele der Snapshots"),
    _TableSpec("backup_run_vm_configs", "VM-Konfigurationen der Backup-Läufe"),
)

# BackupRun.status wird von SQLAlchemy als Enum-NAME gespeichert.
_FINISHED_RUN_FILTER = "status NOT IN ('PENDING', 'RUNNING', 'CLEANING_UP')"

# Tabellen, deren Inhalt die Installation als "nicht mehr leer" kennzeichnet.
_EMPTINESS_TABLES: tuple[tuple[str, str], ...] = (
    ("hyperv_clusters", "Hyper-V-Cluster"),
    ("netapp_clusters", "NetApp-Systeme"),
    ("backup_policies", "Backup-Policies"),
    ("resource_groups", "Protection Groups"),
    ("schedules", "Zeitpläne"),
    ("backup_runs", "Backup-Läufe"),
)


class ConfigTransferError(ValueError):
    """Ungueltige/inkompatible Exportdatei oder Import nicht erlaubt."""


# --- Hilfsfunktionen -----------------------------------------------------------


def _columns(db: Session, table: str) -> list[str]:
    return [row[1] for row in db.execute(text(f"PRAGMA table_info({table})"))]


def _rows(db: Session, table: str, where: str | None = None) -> list[dict]:
    if not _columns(db, table):
        return []
    sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
    return [dict(row._mapping) for row in db.execute(text(sql))]


def _clean(spec: _TableSpec, rows: list[dict]) -> list[dict]:
    reset = dict(spec.reset)
    cleaned = []
    for row in rows:
        row = {k: v for k, v in row.items() if k not in spec.scrub}
        for column, value in reset.items():
            if column in row:
                row[column] = value
        cleaned.append(row)
    return cleaned


def _json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Nicht serialisierbar: {type(value).__name__}")


def installation_blockers(db: Session) -> list[str]:
    """Gruende, warum diese Installation NICHT als leer gilt."""
    blockers = []
    for table, label in _EMPTINESS_TABLES:
        if not _columns(db, table):
            continue
        count = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        if count:
            blockers.append(f"{count} {label}")
    return blockers


# --- Export --------------------------------------------------------------------


def build_export(db: Session, *, include_catalog: bool, exported_by: str, app_commit: str | None) -> tuple[bytes, dict]:
    """Baut das ZIP im Speicher (die Konfiguration ist klein; kein
    Zwischenablegen auf dem Server, siehe Modul-Docstring). Liefert
    (ZIP-Bytes, Manifest)."""
    tables: dict[str, list[dict]] = {}
    for spec in CONFIG_TABLES + USER_TABLES:
        tables[spec.name] = _clean(spec, _rows(db, spec.name))

    if include_catalog:
        runs = _clean(CATALOG_TABLES[0], _rows(db, "backup_runs", _FINISHED_RUN_FILTER))
        run_ids = {r["id"] for r in runs}
        snapshots = [r for r in _rows(db, "backup_run_snapshots") if r.get("run_id") in run_ids]
        snapshot_ids = {r["id"] for r in snapshots}
        tables["backup_runs"] = runs
        tables["backup_run_snapshots"] = snapshots
        tables["backup_run_snapshot_destinations"] = [
            r for r in _rows(db, "backup_run_snapshot_destinations") if r.get("backup_run_snapshot_id") in snapshot_ids
        ]
        tables["backup_run_vm_configs"] = [r for r in _rows(db, "backup_run_vm_configs") if r.get("run_id") in run_ids]

    manifest = {
        "format": "hvnb-config-export",
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": exported_by,
        "app_commit": app_commit,
        "include_catalog": include_catalog,
        "counts": {name: len(rows) for name, rows in tables.items()},
        "manual_steps": manual_steps(tables),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for name, rows in tables.items():
            archive.writestr(f"config/{name}.json", json.dumps(rows, indent=1, ensure_ascii=False, default=_json_default))
    return buffer.getvalue(), manifest


def manual_steps(tables: dict[str, list[dict]]) -> list[str]:
    """Was nach einem Import von Hand nachzutragen ist -- im Manifest
    gespeichert UND in der Import-Vorschau angezeigt."""
    steps: list[str] = []
    hyperv = [r.get("name") for r in tables.get("hyperv_clusters", [])]
    if hyperv:
        steps.append(f"Kennwort je Hyper-V-Cluster neu eintragen (Settings > Hyper-V-Hosts): {', '.join(map(str, hyperv))}")
    netapp_pw = [r.get("name") for r in tables.get("netapp_clusters", []) if (r.get("auth_method") or "").upper() != "CERTIFICATE"]
    netapp_cert = [r.get("name") for r in tables.get("netapp_clusters", []) if (r.get("auth_method") or "").upper() == "CERTIFICATE"]
    if netapp_pw:
        steps.append(f"Kennwort je NetApp-System neu eintragen (Storage > Systeme): {', '.join(map(str, netapp_pw))}")
    if netapp_cert:
        steps.append(
            "Zertifikat-Anmeldung je NetApp-System neu einrichten (Zertifikate werden nicht exportiert): "
            + ", ".join(map(str, netapp_cert))
        )
    if tables.get("restore_proxy_host"):
        steps.append("Kennwort des Restore-Proxy-Hosts neu eintragen (Restore > Setup)")
    if any(r.get("smtp_username") for r in tables.get("email_config", [])):
        steps.append("SMTP-Kennwort neu eintragen, falls der Mailserver eine Anmeldung verlangt (Settings > E-Mail)")
    if any(r.get("share_path") for r in tables.get("db_backup_config", [])):
        steps.append("Kennwort der Freigabe fuer die DB-Sicherung neu eintragen (Settings > DB-Sicherung)")
    if any(r.get("bind_user") for r in tables.get("ad_config", [])):
        steps.append("Kennwort des AD-Lesekontos neu eintragen (Settings > Active Directory)")
    local_users = [
        r.get("username") for r in tables.get("users", [])
        if (r.get("source") or "").upper() == "LOCAL" and r.get("username") != "admin"
    ]
    if local_users:
        steps.append(f"Kennwort lokaler Benutzer neu setzen (Settings > Benutzer & Rollen): {', '.join(map(str, local_users))}")
    if tables.get("winrm_host_certificates"):
        steps.append("WinRM-Zertifikatsbundle wird beim Import automatisch neu erzeugt -- nichts zu tun")
    if tables.get("resource_groups"):
        steps.append(
            "Alle Protection Groups werden beim Import pausiert, damit keine Backups ohne Zugangsdaten starten -- "
            "nach Kennwort-Eintrag und erfolgreicher Discovery wieder aktivieren (Backup > Protection Groups)"
        )
    return steps


# --- Import --------------------------------------------------------------------


@dataclass
class ImportPlan:
    manifest: dict
    tables: dict[str, list[dict]]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> list[dict]:
        labels = {s.name: s.label for s in CONFIG_TABLES + USER_TABLES + CATALOG_TABLES}
        return [
            {"table": name, "label": labels.get(name, name), "count": len(rows)}
            for name, rows in self.tables.items() if rows
        ]


def read_export(data: bytes) -> tuple[dict, dict[str, list[dict]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ConfigTransferError("Die Datei ist kein gültiges ZIP-Archiv.") from exc
    with archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except KeyError as exc:
            raise ConfigTransferError("manifest.json fehlt -- keine Export-Datei dieser Anwendung.") from exc
        if manifest.get("format") != "hvnb-config-export":
            raise ConfigTransferError("Unbekanntes Dateiformat -- keine Export-Datei dieser Anwendung.")
        version = manifest.get("format_version")
        if not isinstance(version, int) or version > FORMAT_VERSION:
            raise ConfigTransferError(
                f"Die Export-Datei hat Formatversion {version}, diese Installation versteht höchstens {FORMAT_VERSION}. "
                "Bitte zuerst die Anwendung aktualisieren."
            )
        known = {s.name for s in CONFIG_TABLES + USER_TABLES + CATALOG_TABLES}
        tables: dict[str, list[dict]] = {}
        for name in archive.namelist():
            if not (name.startswith("config/") and name.endswith(".json")):
                continue
            table = name[len("config/"):-len(".json")]
            if table not in known:
                continue  # z.B. aus einer spaeteren Version -- ignorieren statt scheitern
            rows = json.loads(archive.read(name))
            if not isinstance(rows, list):
                raise ConfigTransferError(f"{name} ist beschädigt (keine Liste).")
            tables[table] = rows
    return manifest, tables


def plan_import(db: Session, data: bytes) -> ImportPlan:
    manifest, tables = read_export(data)
    plan = ImportPlan(manifest=manifest, tables=tables)
    blockers = installation_blockers(db)
    if blockers:
        plan.blockers.append(
            "Diese Installation ist nicht leer (" + ", ".join(blockers) + "). "
            "Ein Import ist nur in eine frische Installation möglich."
        )
    for table, rows in tables.items():
        existing = set(_columns(db, table))
        unknown = sorted({k for row in rows for k in row} - existing) if existing else []
        if not existing:
            plan.warnings.append(f"Tabelle '{table}' existiert in dieser Version nicht -- wird übersprungen.")
        elif unknown:
            plan.warnings.append(f"Unbekannte Felder in '{table}' werden ignoriert: {', '.join(unknown)}")
    return plan


def _insert(db: Session, table: str, row: dict, columns: set[str]) -> None:
    values = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items() if k in columns}
    if not values:
        return
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    db.execute(text(f"INSERT INTO {table} ({names}) VALUES ({params})"), values)


@dataclass
class ImportResult:
    imported: dict[str, int] = field(default_factory=dict)
    paused_groups: list[str] = field(default_factory=list)
    skipped_users: list[str] = field(default_factory=list)


def apply_import(db: Session, plan: ImportPlan) -> ImportResult:
    """Schreibt den Export in EINER Transaktion in diese (leere)
    Installation. Einstellungs-Singletons und die Standard-SnapMirror-Labels
    einer frischen Installation werden ersetzt; Benutzer/Rollen werden per
    Name zusammengefuehrt (der bei der Installation angelegte 'admin' und die
    Standardrollen bleiben mit ihrer ID bestehen, Zuweisungen werden
    umgehaengt)."""
    if plan.blockers:
        raise ConfigTransferError(" ".join(plan.blockers))
    result = ImportResult()
    # Rohformat, in dem SQLAlchemy DateTime-Spalten in SQLite ablegt (naiv, UTC).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

    try:
        for spec in CONFIG_TABLES + CATALOG_TABLES:
            rows = plan.tables.get(spec.name)
            if rows is None:
                continue
            columns = set(_columns(db, spec.name))
            if not columns:
                continue
            # Leere Installation: nur Singletons/Standardwerte vorhanden --
            # durch den Export-Stand ersetzen statt doppelt anzulegen.
            db.execute(text(f"DELETE FROM {spec.name}"))
            for row in _clean(spec, rows):
                if spec.name == "resource_groups" and not row.get("paused"):
                    # Keine Backups ohne Zugangsdaten -- siehe manual_steps.
                    row = {**row, "paused": 1, "paused_since": now}
                    result.paused_groups.append(str(row.get("name")))
                if spec.name == "report_definitions":
                    # Zeitplan beginnt mit dem naechsten Termin -- sonst wuerde der
                    # zuletzt verpasste Termin direkt nach dem Import nachgeholt.
                    row = {**row, "last_scheduled_for": now}
                _insert(db, spec.name, row, columns)
            result.imported[spec.name] = len(rows)

        _import_users_and_roles(db, plan.tables, result)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def _import_users_and_roles(db: Session, tables: dict[str, list[dict]], result: ImportResult) -> None:
    role_id_map: dict[str, str] = {}
    existing_roles = {r["name"]: r["id"] for r in _rows(db, "roles")}
    role_columns = set(_columns(db, "roles"))
    for row in tables.get("roles", []):
        if row.get("name") in existing_roles:
            # Standardrolle der frischen Installation -- deren ID behalten,
            # Rechte kommen weiterhin aus _sync_default_role_permissions.
            role_id_map[row["id"]] = existing_roles[row["name"]]
            if not row.get("is_system_role"):
                permissions = row.get("permissions")
                db.execute(
                    text("UPDATE roles SET description = :d, permissions = :p WHERE id = :id"),
                    {
                        "d": row.get("description") or "",
                        "p": permissions if isinstance(permissions, str) else json.dumps(permissions or []),
                        "id": existing_roles[row["name"]],
                    },
                )
        else:
            _insert(db, "roles", row, role_columns)
            role_id_map[row["id"]] = row["id"]
    result.imported["roles"] = len(tables.get("roles", []))

    user_id_map: dict[str, str] = {}
    existing_users = {u["username"].lower(): u["id"] for u in _rows(db, "users")}
    user_columns = set(_columns(db, "users"))
    for row in _clean(USER_TABLES[1], tables.get("users", [])):
        key = str(row.get("username", "")).lower()
        if key in existing_users:
            # z.B. der bei der Installation angelegte 'admin' -- Konto und
            # Kennwort dieser Installation behalten.
            user_id_map[row["id"]] = existing_users[key]
            result.skipped_users.append(str(row.get("username")))
            continue
        _insert(db, "users", row, user_columns)
        user_id_map[row["id"]] = row["id"]
    result.imported["users"] = len(tables.get("users", [])) - len(result.skipped_users)

    existing_assignments = {
        (a["user_id"], a["role_id"], a.get("scope_type")) for a in _rows(db, "role_assignments")
    }
    assignment_columns = set(_columns(db, "role_assignments"))
    imported_assignments = 0
    for row in tables.get("role_assignments", []):
        user_id = user_id_map.get(row.get("user_id"))
        role_id = role_id_map.get(row.get("role_id"))
        if not user_id or not role_id:
            continue
        key = (user_id, role_id, row.get("scope_type"))
        if key in existing_assignments:
            continue
        existing_assignments.add(key)
        _insert(db, "role_assignments", {**row, "user_id": user_id, "role_id": role_id}, assignment_columns)
        imported_assignments += 1
    result.imported["role_assignments"] = imported_assignments
