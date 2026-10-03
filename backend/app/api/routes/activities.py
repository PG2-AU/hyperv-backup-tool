"""Aktivitaeten-Fusszeile (Backlog #83, Nutzer-Vorgabe 2026-10-03: wie
"Kuerzlich bearbeitete Aufgaben" im vCenter, ohne Alarme-Reiter): alle
Hintergrund-Ablaeufe der App in EINER Liste -- laufende plus die der letzten
24 Stunden. Jede Ablauf-Tabelle hat dieselbe Form (Status, Start, Ende,
Initiator, Schritt-Protokoll), hier auf ein gemeinsames Format gebracht.

GET /api/activities: Liste (Aufgabe, Ziel, Status, aktueller Schritt bzw.
Fortschritt, Initiator, Start, Ende). GET /api/activities/{kind}/{id}: das
Schritt-Protokoll eines Ablaufs; bei einem fehlgeschlagenen Anlage-Lauf mit
offener Rueckfrage (CSV/SMB3/VM anlegen) zusaetzlich die Pfade fuer
"Zurueckrollen"/"Behalten" -- die Rueckfrage geht so nicht mehr verloren,
wenn der Dialog ohne Wahl geschlossen wurde. Rein aus der DB (plus die im
Speicher gehaltenen VM-Power-Aktionen)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes import vm_power
from app.db.session import get_db
from app.models.backup_run import BackupRun, BackupRunStep, JobStatus
from app.models.csv_create_run import CsvCreateRun, CsvCreateRunStep
from app.models.csv_delete_run import CsvDeleteRun, CsvDeleteRunStep
from app.models.csv_resize_run import CsvResizeRun, CsvResizeRunStep
from app.models.file_restore_run import FileRestoreRun, FileRestoreRunStep
from app.models.restore_run import RestoreRun, RestoreRunStep
from app.models.smb_share_run import SmbCreateRun, SmbCreateRunStep, SmbDeleteRun, SmbDeleteRunStep
from app.models.vm_create_run import VmCreateRun, VmCreateRunStep
from app.models.vm_delete_run import VmDeleteRun, VmDeleteRunStep
from app.models.vm_move_run import VmMoveRun, VmMoveRunStep
from app.models.vm_recreate_run import VmRecreateRun, VmRecreateRunStep

router = APIRouter(prefix="/api/activities", tags=["activities"])

WINDOW = timedelta(hours=24)
LIMIT = 200


class Activity(BaseModel):
    kind: str
    id: str
    task: str
    target: str
    # running | succeeded | warning | failed | cleaned_up | cancelled
    status: str
    # Aktueller Schritt (laufend) bzw. Fehler-/Ergebnistext (beendet).
    detail: str | None = None
    progress_percent: int | None = None
    initiator: str
    started_at: datetime
    finished_at: datetime | None = None
    # Fehlgeschlagener Anlage-Lauf mit offener Rueckfrage Zurueckrollen/Behalten.
    needs_decision: bool = False


class ActivityStep(BaseModel):
    label: str
    status: str
    message: str | None = None


class ActivityDetail(BaseModel):
    activity: Activity
    steps: list[ActivityStep]
    error_message: str | None = None
    # API-Pfade (ohne /api) fuer die Rueckfrage, nur bei needs_decision.
    rollback_path: str | None = None
    keep_path: str | None = None
    # Abbrechen moeglich (laufendes Backup, laufender Storage-Move); seit der
    # Fusszeile entfaellt die Backup-Anzeige in der Kopfzeile, die das bisher bot.
    cancel_path: str | None = None
    cancel_requested: bool = False


@dataclass
class _Kind:
    model: type
    step_model: type | None
    task: object  # (run) -> str
    target: object  # (run) -> str
    # Pfad-Praefix fuer Zurueckrollen/Behalten (nur Anlage-Laeufe).
    decision_prefix: str | None = None


_RUN_STATUS = {"running": "running", "succeeded": "succeeded", "failed": "failed", "cleaned_up": "cleaned_up"}
_JOB_STATUS = {
    JobStatus.PENDING: "running", JobStatus.RUNNING: "running", JobStatus.CLEANING_UP: "running",
    JobStatus.SUCCEEDED: "succeeded", JobStatus.SUCCEEDED_WITH_ERRORS: "warning", JobStatus.FAILED: "failed",
    JobStatus.CLEANED_UP_AFTER_FAILURE: "failed", JobStatus.CANCELLED: "cancelled",
}
_MODE_LABEL = {"add": "Disk anhängen", "replace": "Disk ersetzen", "side_by_side": "Side-by-side"}


def _targets(run: BackupRun) -> str:
    targets = run.targets or []
    return ", ".join(targets[:3]) + (f" +{len(targets) - 3}" if len(targets) > 3 else "")


KINDS: dict[str, _Kind] = {
    "backup": _Kind(BackupRun, BackupRunStep, lambda r: f"Backup {r.policy_name}", _targets),
    "restore": _Kind(
        RestoreRun, RestoreRunStep, lambda r: f"VM wiederherstellen ({_MODE_LABEL.get(str(r.mode), r.mode)})", lambda r: r.vm_name,
    ),
    "recreate": _Kind(VmRecreateRun, VmRecreateRunStep, lambda r: "VM neu erstellen", lambda r: r.target_vm_name or r.vm_name),
    "file_restore": _Kind(FileRestoreRun, FileRestoreRunStep, lambda r: "Datei-Restore", lambda r: r.vm_name),
    "vm_move": _Kind(
        VmMoveRun, VmMoveRunStep,
        lambda r: "VM verschieben (Storage)" if r.move_type == "storage" else "VM verschieben (Host)",
        lambda r: f"{r.vm_name} → " + (
            r.destination_csv_name or (f"\\\\{r.destination_smb_server}\\{r.destination_smb_share}" if r.destination_smb_share else r.target_node)
        ),
    ),
    "csv_resize": _Kind(CsvResizeRun, CsvResizeRunStep, lambda r: "CSV vergrößern", lambda r: r.csv_name),
    "csv_create": _Kind(CsvCreateRun, CsvCreateRunStep, lambda r: "CSV anlegen", lambda r: r.csv_name, "/csv-create/runs/"),
    "csv_delete": _Kind(CsvDeleteRun, CsvDeleteRunStep, lambda r: "CSV löschen", lambda r: r.csv_name),
    "smb_create": _Kind(SmbCreateRun, SmbCreateRunStep, lambda r: "SMB3-Freigabe anlegen", lambda r: r.share_name, "/smb-create/runs/"),
    "smb_delete": _Kind(SmbDeleteRun, SmbDeleteRunStep, lambda r: "SMB3-Freigabe löschen", lambda r: f"\\\\{r.server}\\{r.share}"),
    "vm_create": _Kind(VmCreateRun, VmCreateRunStep, lambda r: "VM anlegen", lambda r: r.vm_name, "/vm-create/runs/"),
    "vm_delete": _Kind(VmDeleteRun, VmDeleteRunStep, lambda r: "VM löschen", lambda r: r.vm_name),
}


def _status(run) -> str:
    raw = run.status
    if isinstance(raw, JobStatus):
        return _JOB_STATUS.get(raw, "running")
    value = raw.value if hasattr(raw, "value") else str(raw)
    if value == "succeeded" and getattr(run, "error_message", None) and str(run.error_message).startswith("Mit Warnungen"):
        return "warning"
    return _RUN_STATUS.get(value, value)


def _needs_decision(kind: _Kind, run) -> bool:
    return bool(
        kind.decision_prefix and _status(run) == "failed" and getattr(run, "has_created_objects", False)
        and not getattr(run, "rollback_declined", False)
    )


def _to_activity(kind_name: str, kind: _Kind, run, current_step: str | None) -> Activity:
    state = _status(run)
    detail = current_step if state == "running" else (getattr(run, "error_message", None) or None)
    return Activity(
        kind=kind_name, id=run.id, task=kind.task(run), target=kind.target(run) or "–", status=state,
        detail=(detail or None) and str(detail)[:300], progress_percent=getattr(run, "progress_percent", None) if state == "running" else None,
        initiator=getattr(run, "requested_by", None) or "System", started_at=run.started_at, finished_at=run.finished_at,
        needs_decision=_needs_decision(kind, run),
    )


def _running_step_labels(db: Session, kind: _Kind, run_ids: list[str]) -> dict[str, str]:
    if not kind.step_model or not run_ids:
        return {}
    labels: dict[str, str] = {}
    for step in (
        db.query(kind.step_model)
        .filter(kind.step_model.run_id.in_(run_ids), kind.step_model.status == "running")
        .all()
    ):
        labels[step.run_id] = step.label
    return labels


@router.get("", response_model=list[Activity])
def list_activities(db: Session = Depends(get_db), user=Depends(get_current_user)) -> list[Activity]:
    since = datetime.now(timezone.utc) - WINDOW
    result: list[Activity] = []
    for name, kind in KINDS.items():
        model = kind.model
        runs = (
            db.query(model)
            .filter((model.started_at >= since) | (model.finished_at.is_(None)) | (model.finished_at >= since))
            .order_by(model.started_at.desc())
            .limit(LIMIT)
            .all()
        )
        if kind.decision_prefix:
            # Offene Rueckfragen bleiben sichtbar, egal wie alt -- bis jemand
            # Zurueckrollen oder Behalten waehlt.
            known = {r.id for r in runs}
            runs += [
                r for r in db.query(model).filter(model.status == "failed", model.rollback_declined.is_(False)).all()
                if r.id not in known and _needs_decision(kind, r)
            ]
        # Alte, nie sauber beendete Laeufe (finished_at leer, aber nicht mehr
        # laufend) nicht ewig mitschleppen.
        runs = [r for r in runs if r.started_at >= since or _status(r) == "running" or _needs_decision(kind, r)]
        steps = _running_step_labels(db, kind, [r.id for r in runs if _status(r) == "running"])
        result.extend(_to_activity(name, kind, r, steps.get(r.id)) for r in runs)
    with vm_power._lock:
        power = list(vm_power._actions.values())
    for action in power:
        result.append(Activity(
            kind="vm_power", id=action.id, task=f"VM {vm_power._LABEL[action.action].lower()}", target=action.vm_name,
            status=action.status, detail=action.error_message if action.status == "failed" else (
                f"Status {action.state_after}" if action.state_after else None
            ),
            initiator=action.requested_by or "System", started_at=action.started_at, finished_at=action.finished_at,
        ))
    # Laufende zuerst, dann offene Rueckfragen, dann nach Startzeit (neueste oben).
    result.sort(key=lambda a: (a.status != "running", not a.needs_decision, -a.started_at.timestamp()))
    return result[:LIMIT]


@router.get("/{kind_name}/{run_id}", response_model=ActivityDetail)
def get_activity(kind_name: str, run_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)) -> ActivityDetail:
    kind = KINDS.get(kind_name)
    if kind is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Ablauf-Typ")
    run = db.get(kind.model, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ablauf nicht gefunden")
    steps = []
    if kind.step_model is not None:
        rows = db.query(kind.step_model).filter(kind.step_model.run_id == run_id).order_by(kind.step_model.created_at).all()
        steps = [ActivityStep(label=s.label, status=str(getattr(s.status, "value", s.status)), message=s.message) for s in rows]
    running = next((s.label for s in steps if s.status == "running"), None)
    activity = _to_activity(kind_name, kind, run, running)
    if activity.status != "running":
        # Sicherheitsnetz: ein beendeter Lauf hat keine laufenden Schritte mehr
        # (Altbestand vor close_open_steps bzw. unerwartete Abbrueche).
        for s in steps:
            if s.status == "running":
                s.status = "error"
            elif s.status == "pending":
                s.status = "skipped"
    decision = activity.needs_decision
    cancel_path = None
    if activity.status == "running":
        if kind_name == "backup":
            cancel_path = f"/jobs/runs/{run_id}/cancel"
        elif kind_name == "vm_move" and run.move_type == "storage":
            cancel_path = f"/vm-moves/{run_id}/cancel"
    return ActivityDetail(
        cancel_path=cancel_path, cancel_requested=bool(getattr(run, "cancel_requested_at", None)),
        activity=activity, steps=steps, error_message=getattr(run, "error_message", None),
        rollback_path=f"{kind.decision_prefix}{run_id}/rollback" if decision else None,
        keep_path=f"{kind.decision_prefix}{run_id}/keep" if decision else None,
    )
