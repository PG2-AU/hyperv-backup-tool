"""Legt bei Erststart die DB-Tabellen sowie Standardrollen und einen
initialen lokalen Admin-Benutzer an (Passwort ueber ENV/.env steuerbar)."""

import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.rbac import DEFAULT_ROLES
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import engine
from app.models.role import Role, RoleAssignment
from app.models.snapmirror_label import DEFAULT_SNAPMIRROR_LABELS, SnapMirrorLabel
from app.models.user import User, UserSource


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
