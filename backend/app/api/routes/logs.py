"""System Log: liefert entweder die Schritte eines konkreten Backup-Laufs/
einer Policy (siehe 'context' unten, genutzt von "Log anzeigen" in Backup >
Policies/Job-Verlauf), oder -- ohne 'context' -- eine global zusammengefuehrte
Uebersicht aller Meldungen des Systems innerhalb eines waehlbaren Zeitraums:
gelaufene Backups (BackupRunStep), Restores/VM-Neuerstellungen/Datei-Restores
(deren jeweilige *RunStep-Tabellen) sowie Hintergrund-Jobs (SystemLogEvent --
Health-Check, Discovery, Snapshot-Abgleich, Retention-Cleanup, geplante
Backups, Datei-Restore-Sicherheitsnetz, siehe app.core.scheduler). Das ist der
"System Log"-Button in der Kopfzeile.

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
gemeldeter Bug, 2026-08-29). Die globale Ansicht (ohne context) lieferte
bis 2026-08-31 ebenfalls immer eine leere Liste -- der Kopfzeilen-Button hiess
entsprechend nur "Troubleshooting-Log" und zeigte praktisch nie etwas an."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy
from app.models.backup_run import BackupRun, BackupRunStep
from app.models.file_restore_run import FileRestoreRun, FileRestoreRunStep
from app.models.restore_run import RestoreRun, RestoreRunStep, RestoreStepStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_recreate_run import VmRecreateRun, VmRecreateRunStep
from app.schemas.log import LogEntry, LogLevel

router = APIRouter(prefix="/api/logs", tags=["logs"])

_MAX_RUNS_FOR_POLICY = 10
_MAX_GLOBAL_ENTRIES_PER_TYPE = 400

_LEVEL_BY_STATUS = {
    RestoreStepStatus.SUCCESS: LogLevel.INFO,
    RestoreStepStatus.ERROR: LogLevel.ERROR,
    RestoreStepStatus.SKIPPED: LogLevel.WARNING,
    RestoreStepStatus.RUNNING: LogLevel.DEBUG,
    RestoreStepStatus.PENDING: LogLevel.DEBUG,
}


def _step_message(step, prefix: str) -> str:
    message = f"{prefix}{step.label}"
    if step.message and step.status != RestoreStepStatus.SUCCESS:
        message += f": {step.message}"
    elif step.message and step.status == RestoreStepStatus.SUCCESS:
        message += f" -- {step.message}"
    return message


def _steps_to_entries(steps: list[BackupRunStep], run_by_id: dict[str, BackupRun], source: str = "backup") -> list[LogEntry]:
    entries = []
    for step in steps:
        run = run_by_id.get(step.run_id)
        prefix = f"[{run.policy_name}] " if run and len(run_by_id) > 1 else ""
        entries.append(
            LogEntry(
                timestamp=step.created_at,
                level=_LEVEL_BY_STATUS.get(step.status, LogLevel.INFO),
                source=source,
                context=step.run_id,
                message=_step_message(step, prefix),
            )
        )
    return entries


def _generic_steps_to_entries(steps: list, run_by_id: dict[str, object], source: str) -> list[LogEntry]:
    """Wie _steps_to_entries, aber fuer Restore-/VM-Neuerstellungs-/
    Datei-Restore-Schritte -- deren *Run-Modelle haben statt policy_name ein
    vm_name-Feld, ueber das die Meldung eindeutig einer VM zugeordnet wird."""
    entries = []
    for step in steps:
        run = run_by_id.get(step.run_id)
        vm_name = getattr(run, "vm_name", None) if run else None
        prefix = f"[{vm_name}] " if vm_name else ""
        entries.append(
            LogEntry(
                timestamp=step.created_at,
                level=_LEVEL_BY_STATUS.get(step.status, LogLevel.INFO),
                source=source,
                context=step.run_id,
                message=_step_message(step, prefix),
            )
        )
    return entries


def _system_events_to_entries(events: list[SystemLogEvent]) -> list[LogEntry]:
    return [
        LogEntry(
            timestamp=event.timestamp,
            level=LogLevel(event.level) if event.level in LogLevel.__members__ else LogLevel.INFO,
            source=event.source,
            message=event.message,
        )
        for event in events
    ]


def _global_entries(db: Session, since: datetime) -> list[LogEntry]:
    backup_steps = (
        db.query(BackupRunStep)
        .filter(BackupRunStep.created_at >= since)
        .order_by(BackupRunStep.created_at.desc())
        .limit(_MAX_GLOBAL_ENTRIES_PER_TYPE)
        .all()
    )
    backup_runs = {r.id: r for r in db.query(BackupRun).filter(BackupRun.id.in_({s.run_id for s in backup_steps})).all()}

    restore_steps = (
        db.query(RestoreRunStep)
        .filter(RestoreRunStep.created_at >= since)
        .order_by(RestoreRunStep.created_at.desc())
        .limit(_MAX_GLOBAL_ENTRIES_PER_TYPE)
        .all()
    )
    restore_runs = {r.id: r for r in db.query(RestoreRun).filter(RestoreRun.id.in_({s.run_id for s in restore_steps})).all()}

    recreate_steps = (
        db.query(VmRecreateRunStep)
        .filter(VmRecreateRunStep.created_at >= since)
        .order_by(VmRecreateRunStep.created_at.desc())
        .limit(_MAX_GLOBAL_ENTRIES_PER_TYPE)
        .all()
    )
    recreate_runs = {r.id: r for r in db.query(VmRecreateRun).filter(VmRecreateRun.id.in_({s.run_id for s in recreate_steps})).all()}

    filerestore_steps = (
        db.query(FileRestoreRunStep)
        .filter(FileRestoreRunStep.created_at >= since)
        .order_by(FileRestoreRunStep.created_at.desc())
        .limit(_MAX_GLOBAL_ENTRIES_PER_TYPE)
        .all()
    )
    filerestore_runs = {
        r.id: r for r in db.query(FileRestoreRun).filter(FileRestoreRun.id.in_({s.run_id for s in filerestore_steps})).all()
    }

    system_events = (
        db.query(SystemLogEvent)
        .filter(SystemLogEvent.timestamp >= since)
        .order_by(SystemLogEvent.timestamp.desc())
        .limit(_MAX_GLOBAL_ENTRIES_PER_TYPE)
        .all()
    )

    entries: list[LogEntry] = []
    entries += _steps_to_entries(backup_steps, backup_runs, source="backup")
    entries += _generic_steps_to_entries(restore_steps, restore_runs, source="restore")
    entries += _generic_steps_to_entries(recreate_steps, recreate_runs, source="vm-recreate")
    entries += _generic_steps_to_entries(filerestore_steps, filerestore_runs, source="file-restore")
    entries += _system_events_to_entries(system_events)
    return entries


@router.get("", response_model=list[LogEntry])
def get_logs(
    context: str | None = Query(default=None, description="BackupRun.id oder BackupPolicy.id -- ohne Angabe: globales System Log"),
    hours: int = Query(default=24, ge=1, le=24 * 30, description="Zeitraum in Stunden fuer die globale Ansicht (ohne context)"),
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
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        entries = _global_entries(db, since)

    if level:
        entries = [e for e in entries if e.level == level]
    if search:
        needle = search.lower()
        entries = [e for e in entries if needle in e.message.lower()]
    return sorted(entries, key=lambda e: e.timestamp)
