"""Schutzklassen (Backlog #86): Klassen pflegen (Backup > Schutzklassen),
Objekten zuweisen (dort als Sammelzuweisung und in Inventory je VM/CSV/
SMB3-Freigabe) und das Pruefergebnis abrufen. Pruefung siehe
app.core.protection_class. Lesen: backup:view, Aendern: backup:create."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.protection_class import evaluate, group_fit
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.hyperv_discovery import HyperVVm
from app.models.protection_class import ProtectionClass, ProtectionClassAssignment
from app.models.system_log import SystemLogEvent

router = APIRouter(prefix="/api/protection-classes", tags=["protection-classes"])

_view = require_permission(Permission.BACKUP_VIEW)
_manage = require_permission(Permission.BACKUP_CREATE)


class ClassWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rank: int = Field(ge=1, le=99)
    color: str = Field(default="blue", max_length=20)
    description: str | None = Field(default=None, max_length=500)
    max_backup_age_hours: int = Field(ge=1, le=24 * 366)
    # Aufbewahrung primaer / sekundaer in Tagen; sekundaer 0 = nicht verlangt
    min_retention_days: int = Field(ge=0, le=3660)
    secondary_retention_days: int = Field(default=0, ge=0, le=3660)
    require_app_consistent: bool = False


class ClassRead(ClassWrite):
    id: str
    assigned_count: int = 0


class StorageRead(BaseModel):
    name: str
    class_name: str | None = None
    class_color: str | None = None


class ObjectStatusRead(BaseModel):
    object_type: Literal["vm", "csv", "smb_share"]
    cluster_id: str | None = None
    cluster_name: str | None = None
    name: str
    display_name: str
    class_id: str | None = None
    class_name: str | None = None
    class_color: str | None = None
    status: Literal["ok", "violation", "unassigned"]
    violations: list[str] = []
    notes: list[str] = []
    storage: list[StorageRead] = []
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    last_backup_at: datetime | None = None
    suggested_groups: list[str] = []


class GroupClassFit(BaseModel):
    class_id: str
    class_name: str
    class_color: str | None = None
    fits: bool
    reasons: list[str] = []


class GroupFitRead(BaseModel):
    group_id: str
    group_name: str
    scope: str
    paused: bool
    member_count: int
    classes: list[GroupClassFit]


class AssignmentItem(BaseModel):
    object_type: Literal["vm", "csv", "smb_share"]
    cluster_id: str
    name: str


class AssignRequest(BaseModel):
    # None = Zuordnung entfernen
    class_id: str | None = None
    objects: list[AssignmentItem] = Field(min_length=1, max_length=2000)


def _log(db: Session, message: str) -> None:
    db.add(SystemLogEvent(level="INFO", source="protection-classes", message=message))
    db.commit()


def _user_name(user) -> str:
    return user.display_name or user.username


def _read(db: Session, cls: ProtectionClass) -> ClassRead:
    count = db.query(ProtectionClassAssignment).filter(ProtectionClassAssignment.class_id == cls.id).count()
    return ClassRead(
        id=cls.id, name=cls.name, rank=cls.rank, color=cls.color, description=cls.description,
        max_backup_age_hours=cls.max_backup_age_hours, min_retention_days=cls.min_retention_days,
        secondary_retention_days=cls.secondary_retention_days or 0, require_app_consistent=cls.require_app_consistent,
        assigned_count=count,
    )


def _check_name(db: Session, name: str, own_id: str | None = None) -> None:
    existing = db.query(ProtectionClass).filter(ProtectionClass.name == name.strip()).first()
    if existing is not None and existing.id != own_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Eine Schutzklasse '{name.strip()}' gibt es bereits.")


@router.get("", response_model=list[ClassRead])
def list_classes(db: Session = Depends(get_db), user=Depends(_view)) -> list[ClassRead]:
    return [_read(db, c) for c in db.query(ProtectionClass).order_by(ProtectionClass.rank, ProtectionClass.name).all()]


@router.post("", response_model=ClassRead, status_code=status.HTTP_201_CREATED)
def create_class(payload: ClassWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> ClassRead:
    _check_name(db, payload.name)
    cls = ProtectionClass(**{**payload.model_dump(), "name": payload.name.strip()})
    db.add(cls)
    db.commit()
    db.refresh(cls)
    _log(db, f"Schutzklasse '{cls.name}' angelegt (durch {_user_name(user)})")
    return _read(db, cls)


@router.get("/status", response_model=list[ObjectStatusRead])
def get_status(db: Session = Depends(get_db), user=Depends(_view)) -> list[ObjectStatusRead]:
    return [ObjectStatusRead(**s.__dict__) for s in evaluate(db)]


@router.get("/group-fit", response_model=list[GroupFitRead])
def get_group_fit(db: Session = Depends(get_db), user=Depends(_view)) -> list[GroupFitRead]:
    """Je Protection Group: welche Schutzklassen ihre Sicherung erfuellt."""
    return [GroupFitRead(**g.__dict__) for g in group_fit(db)]


@router.put("/assignments", status_code=status.HTTP_204_NO_CONTENT)
def assign(payload: AssignRequest, db: Session = Depends(get_db), user=Depends(_manage)) -> None:
    cls = None
    if payload.class_id is not None:
        cls = db.get(ProtectionClass, payload.class_id)
        if cls is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schutzklasse nicht gefunden")
    vm_uuids = {(v.cluster_id, v.name): v.vm_uuid for v in db.query(HyperVVm).all()}
    now = datetime.now(timezone.utc)
    for item in payload.objects:
        vm_uuid = vm_uuids.get((item.cluster_id, item.name)) if item.object_type == "vm" else None
        query = db.query(ProtectionClassAssignment).filter(
            ProtectionClassAssignment.object_type == item.object_type, ProtectionClassAssignment.cluster_id == item.cluster_id,
        )
        existing = query.filter(ProtectionClassAssignment.object_name == item.name).all()
        if vm_uuid:
            existing += [a for a in query.filter(ProtectionClassAssignment.vm_uuid == vm_uuid).all() if a not in existing]
        for old in existing:
            db.delete(old)
        if cls is not None:
            db.add(ProtectionClassAssignment(
                object_type=item.object_type, cluster_id=item.cluster_id, object_name=item.name, vm_uuid=vm_uuid, class_id=cls.id,
                assigned_by=_user_name(user), assigned_at=now,
            ))
    db.commit()
    names = ", ".join(i.name for i in payload.objects[:5]) + (f" und {len(payload.objects) - 5} weitere" if len(payload.objects) > 5 else "")
    _log(db, (f"Schutzklasse '{cls.name}' zugewiesen an" if cls else "Schutzklasse entfernt von") + f" {names} (durch {_user_name(user)})")


@router.put("/{class_id}", response_model=ClassRead)
def update_class(class_id: str, payload: ClassWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> ClassRead:
    cls = db.get(ProtectionClass, class_id)
    if cls is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schutzklasse nicht gefunden")
    _check_name(db, payload.name, own_id=class_id)
    for key, value in payload.model_dump().items():
        setattr(cls, key, value.strip() if key == "name" else value)
    db.commit()
    _log(db, f"Schutzklasse '{cls.name}' geändert (durch {_user_name(user)})")
    return _read(db, cls)


@router.delete("/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class(class_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> None:
    cls = db.get(ProtectionClass, class_id)
    if cls is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schutzklasse nicht gefunden")
    name = cls.name
    count = db.query(ProtectionClassAssignment).filter(ProtectionClassAssignment.class_id == class_id).delete()
    db.delete(cls)
    db.commit()
    _log(db, f"Schutzklasse '{name}' gelöscht, {count} Zuordnung(en) entfernt (durch {_user_name(user)})")
