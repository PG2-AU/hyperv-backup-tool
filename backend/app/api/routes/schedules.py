"""Zeitplan-Verwaltung (Schedules): wiederverwendbare Zeitplaene fuer
Backup-Jobs. Typen: hourly (mehrere feste Uhrzeiten/Tag), daily (eine
Uhrzeit/Tag), weekly (ein Wochentag + Uhrzeit), monthly (ein Tag des
Monats + Uhrzeit)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_job import BackupJob
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleRead, ScheduleWrite

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleRead])
def list_schedules(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[Schedule]:
    return db.query(Schedule).order_by(Schedule.name).all()


@router.post("", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> Schedule:
    if db.query(Schedule).filter(Schedule.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Zeitplan mit diesem Namen existiert bereits")

    schedule = Schedule(**payload.model_dump())
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.put("/{schedule_id}", response_model=ScheduleRead)
def update_schedule(
    schedule_id: str,
    payload: ScheduleWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zeitplan nicht gefunden")

    duplicate = db.query(Schedule).filter(Schedule.name == payload.name, Schedule.id != schedule_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Zeitplan mit diesem Namen existiert bereits")

    for field, value in payload.model_dump().items():
        setattr(schedule, field, value)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zeitplan nicht gefunden")

    referencing_jobs = db.query(BackupJob).filter(BackupJob.schedule_id == schedule_id).all()
    if referencing_jobs:
        names = ", ".join(j.name for j in referencing_jobs)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Zeitplan wird noch von folgenden Jobs verwendet: {names}",
        )

    db.delete(schedule)
    db.commit()
