"""Reports (Backlog #84): Typen, Erzeugen (PDF + CSV), Ablage in der Historie,
Mail-Versand und Zeitplan. Inhalte siehe die Module je Typ, PDF siehe pdf.py.

Ablage: <reports_dir>/<JJJJ>/<MM>/<id>.pdf bzw. .csv, 12 Monate aufbewahrt
(purge_old_reports). Geplante Vorlagen arbeitet run_due_reports ab (alle 5
Minuten vom Scheduler aufgerufen): jeder Termin laeuft genau einmal, ein
verpasster (App war aus) wird beim naechsten Check nachgeholt."""

import csv
import hashlib
import io
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.reports import backup_success, protection, restore_points
from app.core.reports.base import ReportContent, fmt_dt, local_tz
from app.models.report import ReportDefinition, ReportRun

RETENTION_DAYS = 365


@dataclass
class ReportType:
    label: str
    description: str
    uses_period: bool
    build: Callable[[Session, dict, datetime], ReportContent]


REPORT_TYPES: dict[str, ReportType] = {
    "protection_status": ReportType(
        "Schutzstatus", "Je VM, CSV und SMB3-Freigabe: Protection Group, Policies, letztes Backup primär/sekundär, überfällig/ungeschützt.",
        False, protection.build,
    ),
    "backup_success": ReportType(
        "Backup-Erfolg", "Backup-Läufe im Zeitraum je Policy, Erfolgsquote, Fehler und Warnungen mit betroffenen VMs, Vergleich zum Vorzeitraum.",
        True, backup_success.build,
    ),
    "restore_points": ReportType(
        "Wiederherstellungspunkte", "Je VM Anzahl, ältester und neuester Wiederherstellungspunkt primär und sekundär.",
        False, restore_points.build,
    ),
}


def _content_hash(content: ReportContent) -> str:
    data = {k: v for k, v in asdict(content).items() if k not in ("csv_columns", "csv_rows")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _csv_bytes(content: ReportContent) -> bytes:
    # Semikolon + BOM: oeffnet sich in einem deutschen Excel direkt richtig.
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(content.csv_columns)
    writer.writerows(content.csv_rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def generate_report(
    db: Session, report_type: str, params: dict, created_by: str, definition: ReportDefinition | None = None,
) -> ReportRun:
    kind = REPORT_TYPES[report_type]
    now = datetime.now(timezone.utc)
    run = ReportRun(
        id=str(uuid.uuid4()), report_type=report_type, title=kind.label, params=params, created_by=created_by, created_at=now,
        definition_id=definition.id if definition else None, definition_name=definition.name if definition else None,
        emailed_to=[],
    )
    try:
        # erst hier importiert: fehlt ReportLab (pip install beim Update
        # gescheitert), startet die App trotzdem, nur der Report schlaegt fehl
        from app.core.reports.pdf import render_pdf

        content = kind.build(db, params, now)
        content_sha = _content_hash(content)
        pdf = render_pdf(content, created=fmt_dt(now), created_by=created_by, content_sha256=content_sha)
        folder = Path(get_settings().reports_dir) / now.astimezone(local_tz()).strftime("%Y/%m")
        folder.mkdir(parents=True, exist_ok=True)
        pdf_path = folder / f"{run.id}.pdf"
        pdf_path.write_bytes(pdf)
        csv_path = folder / f"{run.id}.csv"
        csv_path.write_bytes(_csv_bytes(content))
        run.title = content.title
        run.subtitle = content.subtitle[:500]
        run.findings = content.findings
        run.findings_text = content.findings_text[:500]
        run.pdf_path, run.csv_path = str(pdf_path), str(csv_path)
        run.size_bytes = len(pdf)
        run.file_sha256 = hashlib.sha256(pdf).hexdigest()
        run.content_sha256 = content_sha
        run.status = "succeeded"
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error_message = str(exc)[:2000]
    db.add(run)
    db.commit()
    return run


def send_report(db: Session, run: ReportRun, recipients: list[str], attach_csv: bool) -> None:
    """PDF (+ optional CSV) per Mail. Fehler landen in run.email_error und
    werden weitergereicht."""
    from app.services.email_service import get_email_config, send_raw_email

    config = get_email_config(db)
    try:
        if config is None or not getattr(config, "smtp_host", None):
            raise RuntimeError("E-Mail ist nicht eingerichtet (Settings > E-Mail)")
        if run.status != "succeeded" or not run.pdf_path:
            raise RuntimeError("Der Report wurde nicht erfolgreich erzeugt")
        stamp = run.created_at.astimezone(local_tz()).strftime("%Y-%m-%d")
        base = f"{run.title}_{stamp}".replace(" ", "_")
        attachments = [(f"{base}.pdf", Path(run.pdf_path).read_bytes(), "application/pdf")]
        if attach_csv and run.csv_path and Path(run.csv_path).exists():
            attachments.append((f"{base}.csv", Path(run.csv_path).read_bytes(), "text/csv"))
        name = run.definition_name or run.title
        subject = f"Report: {name} -- {run.findings_text or ''}".strip(" -")
        text_body = (
            f"{run.title}\n{run.subtitle or ''}\n\nErgebnis: {run.findings_text or '–'}\n\n"
            f"Der vollständige Report liegt als PDF bei.\nInhalts-Prüfsumme (SHA-256): {run.content_sha256}\n"
        )
        html_body = (
            f"<h3 style='margin:0'>{run.title}</h3><p style='color:#475467'>{run.subtitle or ''}</p>"
            f"<p><b>Ergebnis:</b> {run.findings_text or '–'}</p><p>Der vollständige Report liegt als PDF bei.</p>"
            f"<p style='color:#98a2b3;font-size:11px'>Inhalts-Prüfsumme (SHA-256): {run.content_sha256}</p>"
        )
        send_raw_email(config, recipients, subject, html_body, text_body, attachments=attachments)
        run.emailed_to = list(recipients)
        run.email_error = None
    except Exception as exc:
        run.email_error = str(exc)[:1000]
        db.commit()
        raise
    db.commit()


# --- Zeitplan --------------------------------------------------------------------------


def _matches(definition: ReportDefinition, day) -> bool:
    if definition.schedule_type == "daily":
        return True
    if definition.schedule_type == "weekly":
        return day.weekday() == (definition.schedule_day or 0)
    if definition.schedule_type == "monthly":
        return day.day == min(max(definition.schedule_day or 1, 1), 28)
    return False


def _slot(definition: ReportDefinition, day) -> datetime:
    hour, minute = (int(x) for x in (definition.schedule_time or "06:00").split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=local_tz())


def latest_slot(definition: ReportDefinition, now: datetime) -> datetime | None:
    """Juengster Zeitplan-Termin <= now (UTC), None ohne Zeitplan."""
    if definition.schedule_type not in ("daily", "weekly", "monthly"):
        return None
    local_now = now.astimezone(local_tz())
    for offset in range(0, 40):
        day = local_now.date() - timedelta(days=offset)
        if _matches(definition, day) and _slot(definition, day) <= local_now:
            return _slot(definition, day).astimezone(timezone.utc)
    return None


def next_slot(definition: ReportDefinition, now: datetime) -> datetime | None:
    """Naechster Termin > now (UTC) fuer die Anzeige, None ohne Zeitplan."""
    if definition.schedule_type not in ("daily", "weekly", "monthly") or not definition.enabled:
        return None
    local_now = now.astimezone(local_tz())
    for offset in range(0, 40):
        day = local_now.date() + timedelta(days=offset)
        if _matches(definition, day) and _slot(definition, day) > local_now:
            return _slot(definition, day).astimezone(timezone.utc)
    return None


def run_due_reports(db: Session, log) -> None:
    now = datetime.now(timezone.utc)
    for definition in db.query(ReportDefinition).filter(ReportDefinition.enabled.is_(True)).all():
        slot = latest_slot(definition, now)
        if slot is None:
            continue
        reference = definition.last_scheduled_for or definition.created_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        if slot <= reference:
            continue
        definition.last_scheduled_for = slot
        db.commit()
        run = generate_report(db, definition.report_type, definition.params or {}, f"Zeitplan: {definition.name}", definition)
        if run.status != "succeeded":
            log(f"Report '{definition.name}' fehlgeschlagen: {run.error_message}", "ERROR")
            continue
        if not definition.recipients:
            log(f"Report '{definition.name}' erstellt ({run.findings_text})", "INFO")
        elif definition.only_if_findings and not run.findings:
            log(f"Report '{definition.name}' erstellt, nicht versendet (keine Auffälligkeiten)", "INFO")
        else:
            try:
                send_report(db, run, definition.recipients, definition.attach_csv)
                log(f"Report '{definition.name}' erstellt und an {', '.join(definition.recipients)} gesendet", "INFO")
            except Exception as exc:  # noqa: BLE001
                log(f"Report '{definition.name}' erstellt, Versand fehlgeschlagen: {exc}", "ERROR")


def purge_old_reports(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    old = db.query(ReportRun).filter(ReportRun.created_at < cutoff).all()
    for run in old:
        for path in (run.pdf_path, run.csv_path):
            if path:
                Path(path).unlink(missing_ok=True)
        db.delete(run)
    if old:
        db.commit()
    return len(old)
