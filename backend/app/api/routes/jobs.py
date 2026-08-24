"""Backup-Policies (frueher Job-Definitionen) und Job-Laeufe.

Backup-Policies (Name, Zeitplan, Konsistenz, SnapMirror-Verhalten, Retention,
Snapshot Locking) werden in der DB persistiert. Die VM/CSV-Zuordnung erfolgt
ueber ResourceGroups, die mit einer Policy verknuepft werden (siehe
app.api.routes.resource_groups). Die tatsaechliche Job-Ausfuehrung
(HyperVService.create_checkpoint -> NetAppOntapService.create_snapshot ->
SnapMirror-Update -> Checkpoint entfernen; bei Fehler: cleanup_checkpoints
+ cleanup_snapshots) folgt als naechster Schritt -- Job-Laeufe (`_DEMO_RUNS`)
sind daher weiterhin Demo-Daten.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy, BackupScope, ConsistencyType
from app.models.schedule import Schedule
from app.models.snapmirror_label import SnapMirrorLabel
from app.schemas.backup import BackupJobRun, BackupPolicyRead, BackupPolicyWrite, JobStatus

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


def _validate_references(payload: BackupPolicyWrite, db: Session) -> None:
    if payload.schedule_id is not None and db.get(Schedule, payload.schedule_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zeitplan nicht gefunden")
    if payload.snapmirror_label_id is not None and db.get(SnapMirrorLabel, payload.snapmirror_label_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SnapMirror-Label nicht gefunden")


@router.get("", response_model=list[BackupPolicyRead])
def list_jobs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupPolicy]:
    return db.query(BackupPolicy).order_by(BackupPolicy.name).all()


@router.post("", response_model=BackupPolicyRead, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: BackupPolicyWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> BackupPolicy:
    if db.query(BackupPolicy).filter(BackupPolicy.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Policy mit diesem Namen existiert bereits")

    _validate_references(payload, db)

    policy = BackupPolicy(
        name=payload.name,
        schedule_id=payload.schedule_id,
        consistency=ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT,
        snapmirror_update=payload.snapmirror_update,
        snapmirror_label_id=payload.snapmirror_label_id,
        retention_type=payload.retention_type,
        retention_value=payload.retention_value,
        snapshot_locking_enabled=payload.snapshot_locking_enabled,
        snapshot_locking_days=payload.snapshot_locking_days,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.put("/{job_id}", response_model=BackupPolicyRead)
def update_job(
    job_id: str,
    payload: BackupPolicyWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> BackupPolicy:
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")

    duplicate = db.query(BackupPolicy).filter(BackupPolicy.name == payload.name, BackupPolicy.id != job_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Policy mit diesem Namen existiert bereits")

    _validate_references(payload, db)

    policy.name = payload.name
    policy.schedule_id = payload.schedule_id
    policy.consistency = ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT
    policy.snapmirror_update = payload.snapmirror_update
    policy.snapmirror_label_id = payload.snapmirror_label_id
    policy.retention_type = payload.retention_type
    policy.retention_value = payload.retention_value
    policy.snapshot_locking_enabled = payload.snapshot_locking_enabled
    policy.snapshot_locking_days = payload.snapshot_locking_days
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")
    db.delete(policy)
    db.commit()


@router.get("/runs", response_model=list[BackupJobRun])
def list_job_runs(user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupJobRun]:
    return _DEMO_RUNS


@router.post("/{job_id}/run", response_model=BackupJobRun, status_code=status.HTTP_202_ACCEPTED)
def trigger_job_run(
    job_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_RUN)),
) -> BackupJobRun:
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")

    groups = policy.resource_groups
    targets = sorted({member for group in groups for member in group.members})
    scope = groups[0].scope if groups else None

    return BackupJobRun(
        id=f"run-{int(datetime.now(timezone.utc).timestamp())}",
        job_id=policy.id,
        job_name=policy.name,
        status=JobStatus.PENDING,
        started_at=datetime.now(timezone.utc),
        scope=scope,
        targets=targets,
    )
