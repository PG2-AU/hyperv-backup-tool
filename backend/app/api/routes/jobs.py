"""Backup-Job-Definitionen und -Laeufe.

TODO(iteration): Persistenz in DB + tatsaechliche Job-Ausfuehrung
(HyperVService.create_checkpoint -> NetAppOntapService.create_snapshot ->
SnapMirror-Update -> Checkpoint entfernen; bei Fehler: cleanup_checkpoints
+ cleanup_snapshots) folgt als naechster Schritt.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.schemas.backup import (
    BackupJobDefinition,
    BackupJobRun,
    BackupScope,
    ConsistencyType,
    JobStatus,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_DEMO_JOBS = [
    BackupJobDefinition(
        id="job-001",
        name="SQL-Cluster taeglich 02:00",
        scope=BackupScope.VM,
        targets=["APP-SQL01"],
        consistency=ConsistencyType.APPLICATION_CONSISTENT,
        schedule_cron="0 2 * * *",
        snapmirror_label="daily",
        metrocluster_aware=True,
    ),
    BackupJobDefinition(
        id="job-002",
        name="CSV1 stuendlich",
        scope=BackupScope.CSV,
        targets=["CSV1"],
        consistency=ConsistencyType.CRASH_CONSISTENT,
        schedule_cron="0 * * * *",
        snapmirror_label="hourly",
        metrocluster_aware=True,
    ),
]

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


@router.get("", response_model=list[BackupJobDefinition])
def list_jobs(user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupJobDefinition]:
    return _DEMO_JOBS


@router.get("/runs", response_model=list[BackupJobRun])
def list_job_runs(user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupJobRun]:
    return _DEMO_RUNS


@router.post("/{job_id}/run", response_model=BackupJobRun, status_code=status.HTTP_202_ACCEPTED)
def trigger_job_run(job_id: str, user=Depends(require_permission(Permission.BACKUP_RUN))) -> BackupJobRun:
    job = next((j for j in _DEMO_JOBS if j.id == job_id), None)
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
