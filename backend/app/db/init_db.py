"""Legt bei Erststart die DB-Tabellen sowie Standardrollen und einen
initialen lokalen Admin-Benutzer an (Passwort ueber ENV/.env steuerbar)."""

import os
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rbac import DEFAULT_ROLES
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import engine
from app.models.backup_policy import BackupScope
from app.models.hyperv_discovery import HyperVCsv, HyperVVm
from app.models.resource_group import ResourceGroup, ResourceGroupPolicyLink, make_member_key
from app.models.role import Role, RoleAssignment
from app.models.scheduler_config import SchedulerConfig
from app.models.snapmirror_label import DEFAULT_SNAPMIRROR_LABELS, SnapMirrorLabel
from app.models.user import User, UserSource


def _migrate_resource_group_policy_link_schedules(db: Session) -> None:
    """Migriert den Zeitplan zur Resource-Group-Policy-Verknuepfung (neu,
    siehe app.models.resource_group.ResourceGroupPolicyLink) -- Nutzer-
    Ueberlegung: derselbe Zeitplan soll nicht mehr fuer ALLE Policies einer
    Resource Group gelten, sondern pro Verknuepfung individuell (eine
    Resource Group kann so z.B. an eine stuendliche UND eine woechentliche
    Policy gehaengt sein, mit je eigenem Zeitplan). Quelle ist
    BackupPolicy.schedule_id, die urspruengliche, alte Stelle, an der ein
    Zeitplan gepflegt wurde -- Spalte existiert physisch ggf. noch in der DB
    (vom Modell nicht mehr gemappt), wird hier nur per Rohzugriff gelesen.

    Bewusst OHNE Fallback auf ResourceGroup.schedule_id (Zwischenversion
    dieser Migration, Zeitplan pro GANZER Resource Group statt pro
    Verknuepfung): dieser Wert wurde seinerzeit pauschal auf ALLE
    Verknuepfungen einer Gruppe angewendet -- fuer eine Verknuepfung, deren
    eigene Policy nie einen Zeitplan hatte (z.B. Resource Group 'PG_Silver'
    mit den Policies 'Silver_Daily' + 'Bronze', nur erstere hatte einen
    Zeitplan), haette der Fallback faelschlich den Zeitplan der JEWEILS
    ANDEREN Policy geerbt. Ohne eigenen Policy-Zeitplan bleibt die
    Verknuepfung daher unveraendert ungeplant (nur manuell ausfuehrbar) --
    korrekt, da das exakt ihrem urspruenglichen Zustand entspricht."""
    cols = [row[1] for row in db.execute(text("PRAGMA table_info(resource_group_policies)"))]
    if "schedule_id" not in cols:
        return

    policy_schedule = {
        row[0]: row[1] for row in db.execute(text("SELECT id, schedule_id FROM backup_policies WHERE schedule_id IS NOT NULL"))
    }
    if not policy_schedule:
        return

    links = db.query(ResourceGroupPolicyLink).filter(ResourceGroupPolicyLink.schedule_id.is_(None)).all()
    dirty = False
    for link in links:
        sched = policy_schedule.get(link.policy_id)
        if sched:
            link.schedule_id = sched
            dirty = True
    if dirty:
        db.commit()


def _cleanup_orphaned_hyperv_discovery_rows(engine) -> None:
    """Loescht HyperVVm/-Vhd/-Csv-Zeilen, deren cluster_id auf keinen mehr
    existierenden HyperVCluster zeigt. Die Modelle sind zwar mit
    ForeignKey(..., ondelete="CASCADE") deklariert, SQLite erzwingt das aber
    nur bei PRAGMA foreign_keys=ON pro Verbindung -- das setzt diese App
    nirgends, die CASCADE-Angabe war also bisher wirkungslos. Ohne diesen
    Cleanup blieben beim Loeschen eines Hyper-V-Clusters dessen VMs/CSVs als
    Stale-Entries stehen (live beobachtet: fuehrte beim erneuten Hinzufuegen
    desselben Clusters zu doppelten VM-Eintraegen im Inventory). Laeuft bei
    JEDEM Start (nicht nur einmalig) -- idempotent, raeumt so auch zukuenftig
    liegen gebliebene Reste aus alten DB-Staenden vor dieser Fix-Version auf."""
    with engine.connect() as conn:
        existing_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(hyperv_clusters)"))]
        if not existing_cols:
            return  # Tabellen existieren noch nicht -- frischer Erststart
        for table in ("hyperv_vhds", "hyperv_vms", "hyperv_csvs"):
            conn.execute(text(f"DELETE FROM {table} WHERE cluster_id NOT IN (SELECT id FROM hyperv_clusters)"))
        conn.commit()


def _migrate_resource_group_members(db: Session) -> None:
    """Migriert ResourceGroup.members von reinen VM-/CSV-Namen (alt,
    clusteruebergreifend mehrdeutig -- Backlog: 'Gleiche CSV-Namen von
    unterschiedlichen Clustern werden nicht korrekt erkannt') zu
    cluster-qualifizierten Schluesseln (siehe
    app.models.resource_group.make_member_key). Ein bereits migrierter
    Eintrag (enthaelt '::') bleibt unveraendert; ein Name, der aktuell zu
    KEINEM oder zu MEHREREN Clustern passt, bleibt ebenfalls als reiner
    Name stehen (kann nicht sicher aufgeloest werden, ohne zu raten) --
    matcht dann weiter ueber den Legacy-Fallback in resolve_member_key/
    _member_matches, statt die Gruppenmitgliedschaft stillschweigend zu
    verlieren. Laeuft bei jedem Start; bereits migrierte Eintraege werden
    dabei uebersprungen, also gefahrlos wiederholbar."""
    groups = db.query(ResourceGroup).all()
    if not groups:
        return

    vm_clusters_by_name: dict[str, set[str]] = defaultdict(set)
    for vm in db.query(HyperVVm).all():
        vm_clusters_by_name[vm.name].add(vm.cluster_id)
    csv_clusters_by_name: dict[str, set[str]] = defaultdict(set)
    for csv in db.query(HyperVCsv).all():
        csv_clusters_by_name[csv.name].add(csv.cluster_id)

    dirty = False
    for group in groups:
        lookup = vm_clusters_by_name if group.scope == BackupScope.VM else csv_clusters_by_name
        new_members = []
        for member in group.members:
            if "::" in member:
                new_members.append(member)
                continue
            candidates = lookup.get(member, set())
            if len(candidates) == 1:
                new_members.append(make_member_key(next(iter(candidates)), member))
            else:
                new_members.append(member)
        if new_members != group.members:
            group.members = new_members
            dirty = True
    if dirty:
        db.commit()


def _migrate_legacy_backup_policies_start(engine) -> None:
    """SQLite kann bestehende NOT-NULL-Spalten nicht per ALTER TABLE entfernen.
    Das BackupPolicy-Modell hatte frueher ein NOT-NULL-'targets'-Feld (VM/CSV-
    Zuordnung erfolgt jetzt ueber ResourceGroups); eine bereits existierende
    Alt-Tabelle mit dieser Spalte wuerde neue INSERTs ohne 'targets' scheitern
    lassen. Falls vorhanden, wird sie umbenannt, sodass create_all() eine neue
    Tabelle im aktuellen Schema anlegt; die Daten werden danach uebernommen."""
    with engine.connect() as conn:
        existing_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(backup_policies)"))]
        if not existing_cols or "targets" not in existing_cols:
            return
        conn.execute(text("DROP TABLE IF EXISTS backup_policies_legacy"))
        conn.execute(text("ALTER TABLE backup_policies RENAME TO backup_policies_legacy"))
        conn.commit()


def _migrate_legacy_backup_policies_finish(engine) -> None:
    with engine.connect() as conn:
        tables = [row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
        if "backup_policies_legacy" not in tables:
            return

        old_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(backup_policies_legacy)"))}
        new_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(backup_policies)"))}
        shared_cols = ", ".join(sorted(old_cols & new_cols))
        conn.execute(text(f"INSERT INTO backup_policies ({shared_cols}) SELECT {shared_cols} FROM backup_policies_legacy"))
        conn.execute(text("DROP TABLE backup_policies_legacy"))
        conn.commit()


def _add_missing_columns(engine, table: str, column_defs: dict[str, str]) -> None:
    """Ergaenzt fehlende, nullable Spalten auf einer bereits existierenden
    Tabelle per ALTER TABLE ADD COLUMN -- SQLite kann das (anders als
    Entfernen/Umbenennen einer Spalte, siehe _migrate_legacy_backup_policies_*
    oben) ohne Tabellen-Neuaufbau. column_defs: Spaltenname -> SQL-Typ
    (z.B. 'INTEGER', 'BOOLEAN', 'TEXT')."""
    with engine.connect() as conn:
        existing_cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        if not existing_cols:
            return  # Tabelle existiert noch nicht -- create_all() legt sie mit allen Spalten frisch an
        for column, sql_type in column_defs.items():
            if column not in existing_cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
        conn.commit()


def init_db(db: Session) -> None:
    _migrate_legacy_backup_policies_start(engine)
    Base.metadata.create_all(bind=engine)
    _migrate_legacy_backup_policies_finish(engine)
    _add_missing_columns(
        engine, "hyperv_vms",
        {
            "cpu_count": "INTEGER", "generation": "INTEGER",
            "memory_startup_bytes": "INTEGER", "memory_minimum_bytes": "INTEGER", "memory_maximum_bytes": "INTEGER",
            "dynamic_memory_enabled": "BOOLEAN", "network_adapters": "JSON", "pci_devices": "JSON",
        },
    )
    _add_missing_columns(engine, "backup_run_vm_configs", {"hyperv_cluster_id": "VARCHAR(36)"})
    _add_missing_columns(
        engine, "scheduler_status",
        {"last_retention_cleanup_at": "DATETIME", "last_file_restore_expiry_at": "DATETIME", "last_email_summary_sent_date": "VARCHAR(10)"},
    )
    _add_missing_columns(engine, "file_restore_runs", {"clone_volume_uuid": "VARCHAR(36)"})
    _add_missing_columns(
        engine, "netapp_snapmirror_policies",
        {"create_snapshot_on_source": "BOOLEAN", "sync_type": "VARCHAR(30)"},
    )
    _add_missing_columns(
        engine, "vm_recreate_runs",
        {"target_vm_name": "VARCHAR(255)", "disconnect_network": "BOOLEAN", "destination_csv_name": "VARCHAR(255)"},
    )
    _add_missing_columns(engine, "backup_policies", {"email_alert_on_failure": "BOOLEAN"})
    _add_missing_columns(engine, "resource_groups", {"schedule_id": "VARCHAR(36)"})
    _add_missing_columns(engine, "resource_group_policies", {"schedule_id": "VARCHAR(36)"})
    _add_missing_columns(engine, "backup_runs", {"resource_group_id": "VARCHAR(36)", "alert_dismissed_at": "DATETIME"})
    _add_missing_columns(engine, "netapp_luns", {"used_bytes": "INTEGER"})
    _add_missing_columns(
        engine, "alert_config",
        {
            "volume_threshold_percent": "INTEGER", "lun_threshold_percent": "INTEGER",
            "snapmirror_lag_threshold_minutes": "INTEGER", "snapmirror_lag_threshold_hours": "INTEGER", "scope": "VARCHAR(30)",
        },
    )
    with engine.connect() as conn:
        conn.execute(text("UPDATE alert_config SET volume_threshold_percent = 90 WHERE volume_threshold_percent IS NULL"))
        conn.execute(text("UPDATE alert_config SET lun_threshold_percent = 90 WHERE lun_threshold_percent IS NULL"))
        # snapmirror_lag_threshold_minutes war der urspruengliche Feldname
        # (Nutzer-Wunsch: Eingabe in Stunden statt Minuten) -- bereits
        # gespeicherte Minutenwerte in die neue Stunden-Spalte uebernehmen,
        # bevor die alte Spalte (bleibt in SQLite bestehen, wird aber nicht
        # mehr gelesen/geschrieben) ignoriert wird.
        conn.execute(
            text(
                "UPDATE alert_config SET snapmirror_lag_threshold_hours = "
                "CAST(ROUND(snapmirror_lag_threshold_minutes / 60.0) AS INTEGER) "
                "WHERE snapmirror_lag_threshold_hours IS NULL AND snapmirror_lag_threshold_minutes IS NOT NULL"
            )
        )
        conn.execute(text("UPDATE alert_config SET snapmirror_lag_threshold_hours = 4 WHERE snapmirror_lag_threshold_hours IS NULL"))
        # SQLAlchemys Enum-Spalte speichert per Default den Enum-NAMEN, nicht
        # den .value-String (anders als bei String(N)-Spalten mit str-Enums)
        # -- 'ALL' (Name von AlertScope.ALL), nicht 'all'.
        conn.execute(text("UPDATE alert_config SET scope = 'ALL' WHERE scope IS NULL"))
        conn.commit()
    # _add_missing_columns liefert bei bereits vorhandenen Zeilen NULL statt
    # False (kein SQL-Default beim ALTER TABLE) -- BackupPolicyRead.
    # email_alert_on_failure ist aber ein Pflicht-bool (nicht optional), ohne
    # Backfill wuerde list_jobs()/get_job() fuer jede VOR dieser Migration
    # angelegte Policy mit einem Pydantic-Validierungsfehler abbrechen.
    with engine.connect() as conn:
        conn.execute(text("UPDATE backup_policies SET email_alert_on_failure = 0 WHERE email_alert_on_failure IS NULL"))
        conn.commit()

    _cleanup_orphaned_hyperv_discovery_rows(engine)
    _migrate_resource_group_members(db)
    _migrate_resource_group_policy_link_schedules(db)

    for role_name, permissions in DEFAULT_ROLES.items():
        existing = db.query(Role).filter(Role.name == role_name).first()
        if existing is None:
            db.add(
                Role(
                    name=role_name,
                    description=f"Standardrolle: {role_name}",
                    permissions=sorted(p.value for p in permissions),
                    is_system_role=True,
                )
            )
    db.commit()

    admin_exists = db.query(User).filter(User.username == "admin").first()
    if admin_exists is None:
        admin_password = os.environ.get("HVNB_INITIAL_ADMIN_PASSWORD", "ChangeMe123!")
        admin = User(
            username="admin",
            display_name="Administrator",
            source=UserSource.LOCAL,
            hashed_password=hash_password(admin_password),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        admin_role = db.query(Role).filter(Role.name == "Administrator").first()
        db.add(RoleAssignment(user_id=admin.id, role_id=admin_role.id, scope_type="global"))
        db.commit()

    for label_name in DEFAULT_SNAPMIRROR_LABELS:
        existing_label = db.query(SnapMirrorLabel).filter(SnapMirrorLabel.name == label_name).first()
        if existing_label is None:
            db.add(SnapMirrorLabel(name=label_name))
    db.commit()

    # SchedulerConfig-Singleton: Startwerte aus den bisherigen ENV-Variablen
    # uebernehmen (siehe app.core.config), damit eine bereits ueber .env
    # angepasste Instanz beim Umstieg auf die GUI-Konfiguration (Settings >
    # Hintergrundjobs) nicht stillschweigend auf die Hardcoded-Defaults
    # zurueckfaellt. retention_cleanup_hour hatte vorher kein eigenes Feld
    # (lief immer 15min nach snapshot_reconcile_hour) -- als Startwert daher
    # bewusst derselbe Wert.
    if db.query(SchedulerConfig).first() is None:
        settings = get_settings()
        db.add(
            SchedulerConfig(
                healthcheck_interval_minutes=settings.healthcheck_interval_minutes,
                discovery_interval_minutes=settings.discovery_interval_minutes,
                snapshot_reconcile_hour=settings.snapshot_reconcile_hour,
                retention_cleanup_hour=settings.snapshot_reconcile_hour,
            )
        )
        db.commit()
