"""Job-Log: liefert die tatsaechlich waehrend eines Backup-Laufs
durchgefuehrten Schritte (BackupRunStep, siehe trigger_job_run in jobs.py)
als LogEntry-Liste, damit die bestehende LogViewer-Frontend-Komponente
(Filter/Suche/Kopieren) unveraendert weiterverwendet werden kann.

'context' wird auf zwei Arten interpretiert -- je nachdem, von wo "Log
anzeigen" geklickt wurde:
- context == BackupRun.id: nur die Schritte dieses einen Laufs (Backup >
  Job-Verlauf, ein konkreter Lauf).
- context == BackupPolicy.id: die Schritte der letzten Laeufe dieser
  Policy, chronologisch (Backup > Policies, "Log anzeigen" auf einer
  Policy ohne Bezug zu einem bestimmten Lauf).
Passt weder ein Lauf noch eine Policy zur context-ID, wird eine leere
Liste geliefert (z.B. fuer eine geloeschte Policy/einen geloeschten Lauf).

Vorher: reine In-Memory-Demo-Daten, die nie zu einer echten context-ID
passten -- der Log-Viewer war dadurch faktisch immer leer (vom Nutzer
gemeldeter Bug, 2026-08-29)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy
from app.models.backup_run import BackupRun, BackupRunStep
from app.models.restore_run import RestoreStepStatus
from app.schemas.log import LogEntry, LogLevel

router = APIRouter(prefix="/api/logs", tags=["logs"])

_MAX_RUNS_FOR_POLICY = 10

_LEVEL_BY_STATUS = {
    RestoreStepStatus.SUCCESS: LogLevel.INFO,
    RestoreStepStatus.ERROR: LogLevel.ERROR,
    RestoreStepStatus.SKIPPED: LogLevel.WARNING,
    RestoreStepStatus.RUNNING: LogLevel.DEBUG,
    RestoreStepStatus.PENDING: LogLevel.DEBUG,
}


def _steps_to_entries(steps: list[BackupRunStep], run_by_id: dict[str, BackupRun]) -> list[LogEntry]:
    entries = []
    for step in steps:
        run = run_by_id.get(step.run_id)
        prefix = f"[{run.policy_name}] " if run and len(run_by_id) > 1 else ""
        message = f"{prefix}{step.label}"
        if step.message and step.status != RestoreStepStatus.SUCCESS:
            message += f": {step.message}"
        elif step.message and step.status == RestoreStepStatus.SUCCESS:
            message += f" -- {step.message}"
        entries.append(
            LogEntry(
                timestamp=step.created_at,
                level=_LEVEL_BY_STATUS.get(step.status, LogLevel.INFO),
                source="backup",
                context=step.run_id,
                message=message,
            )
        )
    return entries


@router.get("", response_model=list[LogEntry])
def get_logs(
    context: str | None = Query(default=None, description="BackupRun.id oder BackupPolicy.id"),
    level: LogLevel | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.LOGS_VIEW)),
) -> list[LogEntry]:
    entries: list[LogEntry] = []
    if context:
        run = db.get(BackupRun, context)
        if run is not None:
            entries = _steps_to_entries(run.steps, {run.id: run})
        else:
            policy = db.get(BackupPolicy, context)
            if policy is not None:
                runs = (
                    db.query(BackupRun)
                    .filter(BackupRun.policy_id == policy.id)
                    .order_by(BackupRun.started_at.desc())
                    .limit(_MAX_RUNS_FOR_POLICY)
                    .all()
                )
                run_by_id = {r.id: r for r in runs}
                steps = [s for r in runs for s in r.steps]
                entries = _steps_to_entries(steps, run_by_id)

    if level:
        entries = [e for e in entries if e.level == level]
    if search:
        needle = search.lower()
        entries = [e for e in entries if needle in e.message.lower()]
    return sorted(entries, key=lambda e: e.timestamp)
