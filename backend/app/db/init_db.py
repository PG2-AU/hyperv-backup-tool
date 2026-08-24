"""Legt bei Erststart die DB-Tabellen sowie Standardrollen und einen
initialen lokalen Admin-Benutzer an (Passwort ueber ENV/.env steuerbar)."""

import os

from sqlalchemy.orm import Session

from app.core.rbac import DEFAULT_ROLES
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import engine
from app.models.role import Role, RoleAssignment
from app.models.snapmirror_label import DEFAULT_SNAPMIRROR_LABELS, SnapMirrorLabel
from app.models.user import User, UserSource


def init_db(db: Session) -> None:
    Base.metadata.create_all(bind=engine)

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
