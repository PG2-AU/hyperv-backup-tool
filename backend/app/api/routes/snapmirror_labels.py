"""SnapMirror-Label-Verwaltung: benannte Labels, mit denen Snapshots getaggt
werden, damit SnapMirror-Retention-Regeln auf dem Zielsystem greifen. Werden
von Backup-Policies referenziert (siehe app.api.routes.jobs)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy
from app.models.snapmirror_label import SnapMirrorLabel
from app.schemas.snapmirror_label import SnapMirrorLabelRead, SnapMirrorLabelWrite

router = APIRouter(prefix="/api/snapmirror-labels", tags=["snapmirror-labels"])


@router.get("", response_model=list[SnapMirrorLabelRead])
def list_snapmirror_labels(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[SnapMirrorLabel]:
    return db.query(SnapMirrorLabel).order_by(SnapMirrorLabel.name).all()


@router.post("", response_model=SnapMirrorLabelRead, status_code=status.HTTP_201_CREATED)
def create_snapmirror_label(
    payload: SnapMirrorLabelWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> SnapMirrorLabel:
    if db.query(SnapMirrorLabel).filter(SnapMirrorLabel.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Label mit diesem Namen existiert bereits")

    label = SnapMirrorLabel(name=payload.name)
    db.add(label)
    db.commit()
    db.refresh(label)
    return label


@router.put("/{label_id}", response_model=SnapMirrorLabelRead)
def update_snapmirror_label(
    label_id: str,
    payload: SnapMirrorLabelWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> SnapMirrorLabel:
    label = db.get(SnapMirrorLabel, label_id)
    if label is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Label nicht gefunden")

    duplicate = db.query(SnapMirrorLabel).filter(SnapMirrorLabel.name == payload.name, SnapMirrorLabel.id != label_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Label mit diesem Namen existiert bereits")

    label.name = payload.name
    db.commit()
    db.refresh(label)
    return label


@router.delete("/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_snapmirror_label(
    label_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    label = db.get(SnapMirrorLabel, label_id)
    if label is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Label nicht gefunden")

    referencing_policies = db.query(BackupPolicy).filter(BackupPolicy.snapmirror_label_id == label_id).all()
    if referencing_policies:
        names = ", ".join(p.name for p in referencing_policies)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Label wird noch von folgenden Backup-Policies verwendet: {names}",
        )

    db.delete(label)
    db.commit()
