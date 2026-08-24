"""Resource Groups: buendeln VMs oder CSVs zu einer benannten Gruppe (z.B.
'Bronze'), die mit einer oder mehreren Backup-Policies verknuepft wird.
Resource Group + Policy zusammen ergeben die Backup-Definition."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy
from app.models.resource_group import ResourceGroup
from app.schemas.resource_group import ResourceGroupRead, ResourceGroupWrite

router = APIRouter(prefix="/api/resource-groups", tags=["resource-groups"])


def _resolve_policies(policy_ids: list[str], db: Session) -> list[BackupPolicy]:
    if not policy_ids:
        return []
    policies = db.query(BackupPolicy).filter(BackupPolicy.id.in_(policy_ids)).all()
    found_ids = {p.id for p in policies}
    missing = set(policy_ids) - found_ids
    if missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Policy(s) nicht gefunden: {', '.join(missing)}")
    return policies


@router.get("", response_model=list[ResourceGroupRead])
def list_resource_groups(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[ResourceGroup]:
    return db.query(ResourceGroup).order_by(ResourceGroup.name).all()


@router.post("", response_model=ResourceGroupRead, status_code=status.HTTP_201_CREATED)
def create_resource_group(
    payload: ResourceGroupWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> ResourceGroup:
    if db.query(ResourceGroup).filter(ResourceGroup.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Resource Group mit diesem Namen existiert bereits")

    group = ResourceGroup(
        name=payload.name,
        scope=payload.scope,
        members=payload.members,
        policies=_resolve_policies(payload.policy_ids, db),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.put("/{group_id}", response_model=ResourceGroupRead)
def update_resource_group(
    group_id: str,
    payload: ResourceGroupWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> ResourceGroup:
    group = db.get(ResourceGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource Group nicht gefunden")

    duplicate = db.query(ResourceGroup).filter(ResourceGroup.name == payload.name, ResourceGroup.id != group_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Resource Group mit diesem Namen existiert bereits")

    group.name = payload.name
    group.scope = payload.scope
    group.members = payload.members
    group.policies = _resolve_policies(payload.policy_ids, db)
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource_group(
    group_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    group = db.get(ResourceGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource Group nicht gefunden")
    db.delete(group)
    db.commit()
