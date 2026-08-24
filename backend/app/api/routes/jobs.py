"""Backup-Job-Definitionen und -Laeufe.

Job-Definitionen (Name, Zeitplan, Konsistenz, SnapMirror-Update) werden in
der DB persistiert. Die VM/CSV/LUN-Zuordnung (scope/targets) sowie die
tatsaechliche Job-Ausfuehrung (HyperVService.create_checkpoint ->
NetAppOntapService.create_snapshot -> SnapMirror-Update -> Checkpoint
entfernen; bei Fehler: cleanup_checkpoints + cleanup_snapshots) folgen als
naechste Schritte -- Job-Laeufe (`_DEMO_RUNS`) sind daher weiterhin Demo-Daten.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_job import BackupJob, BackupScope, ConsistencyType
from app.models.schedule import Schedule
from app.schemas.backup import BackupJobCreate, BackupJobRead, BackupJobRun, JobStatus

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_DEMO_RUNS = [
    BackupJobRun(
        id="run-1001",
        job_id="job-001",
        job_name="SQL-Cluster taeglich 02:00",
        status=JobStatus.SUCCEEDED,
        started_at=datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 14, 2, 6, tzinfo=timezone.utc),
        scope=BackupScope.VM,
        targets=["APP-SQL01"],
        created_snapshots=["daily.2026-08-14_0200"],
        created_checkpoints=[],
    ),
    BackupJobRun(
        id="run-1002",
        job_id="job-002",
        job_name="CSV1 stuendlich",
        status=JobStatus.CLEANED_UP_AFTER_FAILURE,
        started_at=datetime(2026, 8, 14, 5, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 14, 5, 2, tzinfo=timezone.utc),
        scope=BackupScope.CSV,
        targets=["CSV1"],
        created_snapshots=[],
        created_checkpoints=[],
        error_message="SnapMirror-Update fehlgeschlagen: destination volume offline. Checkpoints wurden automatisch entfernt.",
    ),
]


@router.get("", response_model=list[BackupJobRead])
def list_jobs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupJob]:
    return db.query(BackupJob).order_by(BackupJob.name).all()


@router.post("", response_model=BackupJobRead, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: BackupJobCreate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> BackupJob:
    if db.query(BackupJob).filter(BackupJob.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Job mit diesem Namen existiert bereits")

    if payload.schedule_id is not None and db.get(Schedule, payload.schedule_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zeitplan nicht gefunden")

    job = BackupJob(
        name=payload.name,
        schedule_id=payload.schedule_id,
        consistency=ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT,
        snapmirror_update=payload.snapmirror_update,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job nicht gefunden")
    db.delete(job)
    db.commit()


@router.get("/runs", response_model=list[BackupJobRun])
def list_job_runs(user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupJobRun]:
    return _DEMO_RUNS


@router.post("/{job_id}/run", response_model=BackupJobRun, status_code=status.HTTP_202_ACCEPTED)
def trigger_job_run(
    job_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_RUN)),
) -> BackupJobRun:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job nicht gefunden")

    return BackupJobRun(
        id=f"run-{int(datetime.now(timezone.utc).timestamp())}",
        job_id=job.id,
        job_name=job.name,
        status=JobStatus.PENDING,
        started_at=datetime.now(timezone.utc),
        scope=job.scope,
        targets=job.targets,
    )
