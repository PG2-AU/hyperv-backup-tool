"""Reports (Backlog #84): Menuepunkt "Reports" mit "Neuer Report",
"Gespeicherte Reports" und "Historie". Inhalte, PDF und Zeitplan siehe
app.core.reports.

Rechte: report:view zum Erstellen, Ansehen und Herunterladen; report:manage
fuer Vorlagen, Zeitplaene, Mailversand und Loeschen aus der Historie. Ein
geplanter Report laeuft als "System" und enthaelt alle Daten seiner Auswahl --
Empfaenger brauchen kein Konto in der App."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.core.reports import REPORT_TYPES, generate_report, next_slot, send_report
from app.core.reports.base import PERIOD_PRESETS, local_tz
from app.db.session import get_db
from app.models.report import ReportDefinition, ReportRun
from app.models.system_log import SystemLogEvent

router = APIRouter(prefix="/api/reports", tags=["reports"])

_view = require_permission(Permission.REPORT_VIEW)
_manage = require_permission(Permission.REPORT_MANAGE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ReportTypeRead(BaseModel):
    type: str
    label: str
    description: str
    uses_period: bool


class ReportOptionsRead(BaseModel):
    types: list[ReportTypeRead]
    periods: dict[str, str]
    timezone: str


class GenerateRequest(BaseModel):
    report_type: str
    params: dict = Field(default_factory=dict)


class ReportRunRead(BaseModel):
    id: str
    definition_id: str | None = None
    definition_name: str | None = None
    report_type: str
    title: str
    subtitle: str | None = None
    params: dict
    created_by: str | None = None
    created_at: datetime
    status: str
    error_message: str | None = None
    findings: int
    findings_text: str | None = None
    size_bytes: int | None = None
    has_csv: bool = False
    file_sha256: str | None = None
    content_sha256: str | None = None
    emailed_to: list[str] = []
    email_error: str | None = None


class DefinitionWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    report_type: str
    params: dict = Field(default_factory=dict)
    schedule_type: Literal["none", "daily", "weekly", "monthly"] = "none"
    schedule_day: int | None = None
    schedule_time: str = "06:00"
    recipients: list[str] = Field(default_factory=list)
    only_if_findings: bool = False
    attach_csv: bool = False
    enabled: bool = True


class DefinitionRead(DefinitionWrite):
    id: str
    created_by: str | None = None
    created_at: datetime
    last_scheduled_for: datetime | None = None
    next_run_at: datetime | None = None
    last_run: ReportRunRead | None = None


class RunDefinitionRequest(BaseModel):
    send: bool = False


def _run_read(run: ReportRun) -> ReportRunRead:
    return ReportRunRead(
        **{k: getattr(run, k) for k in (
            "id", "definition_id", "definition_name", "report_type", "title", "subtitle", "created_by", "created_at", "status",
            "error_message", "findings", "findings_text", "size_bytes", "file_sha256", "content_sha256", "email_error",
        )},
        params=run.params or {}, emailed_to=run.emailed_to or [], has_csv=bool(run.csv_path and Path(run.csv_path).exists()),
    )


def _definition_read(db: Session, d: ReportDefinition) -> DefinitionRead:
    last = (
        db.query(ReportRun).filter(ReportRun.definition_id == d.id).order_by(ReportRun.created_at.desc()).first()
    )
    return DefinitionRead(
        id=d.id, name=d.name, report_type=d.report_type, params=d.params or {}, schedule_type=d.schedule_type,
        schedule_day=d.schedule_day, schedule_time=d.schedule_time, recipients=d.recipients or [],
        only_if_findings=d.only_if_findings, attach_csv=d.attach_csv, enabled=d.enabled, created_by=d.created_by,
        created_at=d.created_at, last_scheduled_for=d.last_scheduled_for,
        next_run_at=next_slot(d, datetime.now(timezone.utc)), last_run=_run_read(last) if last else None,
    )


def _user_name(user) -> str:
    return user.display_name or user.username


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="reports", message=message))
    db.commit()


def _validate_definition(payload: DefinitionWrite) -> None:
    errors = []
    if payload.report_type not in REPORT_TYPES:
        errors.append("Unbekannter Report-Typ.")
    if payload.schedule_type != "none":
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", payload.schedule_time or ""):
            errors.append("Uhrzeit bitte als HH:MM angeben.")
        if payload.schedule_type == "weekly" and payload.schedule_day not in range(0, 7):
            errors.append("Wochentag fehlt.")
        if payload.schedule_type == "monthly" and payload.schedule_day not in range(1, 29):
            errors.append("Tag des Monats bitte 1–28.")
    bad = [r for r in payload.recipients if not _EMAIL.match(r.strip())]
    if bad:
        errors.append(f"Ungültige E-Mail-Adresse(n): {', '.join(bad)}.")
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))


# --- Typen / Erzeugen -------------------------------------------------------------------


@router.get("/options", response_model=ReportOptionsRead)
def options(user=Depends(_view)) -> ReportOptionsRead:
    return ReportOptionsRead(
        types=[ReportTypeRead(type=k, label=v.label, description=v.description, uses_period=v.uses_period) for k, v in REPORT_TYPES.items()],
        periods=PERIOD_PRESETS, timezone=str(local_tz()),
    )


@router.post("/generate", response_model=ReportRunRead)
def generate(payload: GenerateRequest, db: Session = Depends(get_db), user=Depends(_view)) -> ReportRunRead:
    if payload.report_type not in REPORT_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unbekannter Report-Typ")
    run = generate_report(db, payload.report_type, payload.params, _user_name(user))
    if run.status != "succeeded":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Report konnte nicht erstellt werden: {run.error_message}")
    return _run_read(run)


# --- Historie ----------------------------------------------------------------------------


@router.get("/runs", response_model=list[ReportRunRead])
def list_runs(limit: int = 500, db: Session = Depends(get_db), user=Depends(_view)) -> list[ReportRunRead]:
    runs = db.query(ReportRun).order_by(ReportRun.created_at.desc()).limit(min(limit, 2000)).all()
    return [_run_read(r) for r in runs]


def _get_run(db: Session, run_id: str) -> ReportRun:
    run = db.get(ReportRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report nicht gefunden")
    return run


def _filename(run: ReportRun, ext: str) -> str:
    stamp = run.created_at.astimezone(local_tz()).strftime("%Y-%m-%d_%H%M")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{run.definition_name or run.title}_{stamp}") + f".{ext}"


@router.get("/runs/{run_id}/pdf")
def download_pdf(run_id: str, inline: bool = True, db: Session = Depends(get_db), user=Depends(_view)) -> FileResponse:
    run = _get_run(db, run_id)
    if not run.pdf_path or not Path(run.pdf_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF nicht mehr vorhanden")
    return FileResponse(
        run.pdf_path, media_type="application/pdf", filename=_filename(run, "pdf"),
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/runs/{run_id}/csv")
def download_csv(run_id: str, db: Session = Depends(get_db), user=Depends(_view)) -> FileResponse:
    run = _get_run(db, run_id)
    if not run.csv_path or not Path(run.csv_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CSV nicht vorhanden")
    return FileResponse(run.csv_path, media_type="text/csv", filename=_filename(run, "csv"))


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> None:
    run = _get_run(db, run_id)
    for path in (run.pdf_path, run.csv_path):
        if path:
            Path(path).unlink(missing_ok=True)
    db.delete(run)
    db.commit()
    _log(db, f"Report '{run.definition_name or run.title}' vom {run.created_at:%d.%m.%Y} aus der Historie gelöscht (durch {_user_name(user)})")


# --- Gespeicherte Reports ------------------------------------------------------------------


@router.get("/definitions", response_model=list[DefinitionRead])
def list_definitions(db: Session = Depends(get_db), user=Depends(_view)) -> list[DefinitionRead]:
    return [_definition_read(db, d) for d in db.query(ReportDefinition).order_by(ReportDefinition.name).all()]


@router.post("/definitions", response_model=DefinitionRead, status_code=status.HTTP_201_CREATED)
def create_definition(payload: DefinitionWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> DefinitionRead:
    _validate_definition(payload)
    d = ReportDefinition(**payload.model_dump(), created_by=_user_name(user))
    d.recipients = [r.strip() for r in payload.recipients if r.strip()]
    db.add(d)
    db.commit()
    db.refresh(d)
    _log(db, f"Report-Vorlage '{d.name}' angelegt (durch {_user_name(user)})")
    return _definition_read(db, d)


@router.put("/definitions/{definition_id}", response_model=DefinitionRead)
def update_definition(definition_id: str, payload: DefinitionWrite, db: Session = Depends(get_db), user=Depends(_manage)) -> DefinitionRead:
    d = db.get(ReportDefinition, definition_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vorlage nicht gefunden")
    _validate_definition(payload)
    schedule_changed = (payload.schedule_type, payload.schedule_day, payload.schedule_time) != (d.schedule_type, d.schedule_day, d.schedule_time)
    for key, value in payload.model_dump().items():
        setattr(d, key, value)
    d.recipients = [r.strip() for r in payload.recipients if r.strip()]
    d.updated_at = datetime.now(timezone.utc)
    if schedule_changed:
        # Neuer Zeitplan: erst ab dem naechsten Termin, nicht rueckwirkend.
        d.last_scheduled_for = datetime.now(timezone.utc)
    db.commit()
    _log(db, f"Report-Vorlage '{d.name}' geändert (durch {_user_name(user)})")
    return _definition_read(db, d)


@router.delete("/definitions/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_definition(definition_id: str, db: Session = Depends(get_db), user=Depends(_manage)) -> None:
    d = db.get(ReportDefinition, definition_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vorlage nicht gefunden")
    name = d.name
    db.delete(d)
    db.commit()
    _log(db, f"Report-Vorlage '{name}' gelöscht (durch {_user_name(user)}); erzeugte Reports bleiben in der Historie")


@router.post("/definitions/{definition_id}/run", response_model=ReportRunRead)
def run_definition(
    definition_id: str, payload: RunDefinitionRequest, db: Session = Depends(get_db), user=Depends(_manage),
) -> ReportRunRead:
    """Jetzt erstellen; mit send=true zusaetzlich an die Empfaenger der Vorlage
    (unabhaengig von "nur bei Auffaelligkeiten" -- bewusst ausgeloest)."""
    d = db.get(ReportDefinition, definition_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vorlage nicht gefunden")
    run = generate_report(db, d.report_type, d.params or {}, _user_name(user), d)
    if run.status != "succeeded":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Report konnte nicht erstellt werden: {run.error_message}")
    if payload.send:
        if not d.recipients:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die Vorlage hat keine Empfänger.")
        try:
            send_report(db, run, d.recipients, d.attach_csv)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Report erstellt, Versand fehlgeschlagen: {exc}") from exc
        _log(db, f"Report '{d.name}' an {', '.join(d.recipients)} gesendet (durch {_user_name(user)})")
    return _run_read(run)
