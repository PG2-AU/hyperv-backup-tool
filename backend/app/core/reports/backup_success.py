"""Report "Backup-Erfolg" (Backlog #84): Backup-Laeufe im Zeitraum je Policy
-- Anzahl, erfolgreich, mit Warnungen, fehlgeschlagen, abgebrochen,
Erfolgsquote, durchschnittliche Dauer -- plus Liste der problematischen Laeufe
mit betroffenen VMs, Vergleich zum Vorzeitraum.

Erfolgsquote = (erfolgreich + mit Warnungen) / (alle ausser abgebrochen):
ein Lauf "mit Warnungen" hat gueltige Storage-Snapshots (restorebar), nur
nicht jede VM app-konsistent; ein manueller Abbruch ist kein Fehler.

Auswahl (params): period (+ period_from/period_to), policy_ids,
resource_group_ids, detail (alle Einzellaeufe auflisten)."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.reports.base import Kpi, ReportContent, Section, fmt_dt, fmt_duration, fmt_num, pct, resolve_period
from app.models.backup_policy import BackupPolicy
from app.models.backup_run import BackupRun, BackupRunStep, JobStatus
from app.models.resource_group import ResourceGroup

_OK = {JobStatus.SUCCEEDED}
_WARN = {JobStatus.SUCCEEDED_WITH_ERRORS}
_FAILED = {JobStatus.FAILED, JobStatus.CLEANED_UP_AFTER_FAILURE}
_CANCELLED = {JobStatus.CANCELLED}
_LABEL = {
    JobStatus.SUCCEEDED: "Erfolgreich", JobStatus.SUCCEEDED_WITH_ERRORS: "Mit Warnungen", JobStatus.FAILED: "Fehlgeschlagen",
    JobStatus.CLEANED_UP_AFTER_FAILURE: "Fehlgeschlagen", JobStatus.CANCELLED: "Abgebrochen",
}


def _runs(db: Session, start: datetime, end: datetime, policy_ids: set[str], group_ids: set[str]) -> list[BackupRun]:
    query = db.query(BackupRun).filter(BackupRun.started_at >= start, BackupRun.started_at < end, BackupRun.finished_at.isnot(None))
    if policy_ids:
        query = query.filter(BackupRun.policy_id.in_(policy_ids))
    if group_ids:
        query = query.filter(BackupRun.resource_group_id.in_(group_ids))
    return query.order_by(BackupRun.started_at).all()


def _rate(runs: list[BackupRun]) -> tuple[int, int]:
    relevant = [r for r in runs if r.status not in _CANCELLED]
    good = [r for r in relevant if r.status in _OK | _WARN]
    return len(good), len(relevant)


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    period = resolve_period(params, now)
    policy_ids = set(params.get("policy_ids") or [])
    group_ids = set(params.get("resource_group_ids") or [])
    runs = _runs(db, period.start, period.end, policy_ids, group_ids)
    previous = _runs(db, period.previous_start, period.previous_end, policy_ids, group_ids)

    by_policy: dict[str, list[BackupRun]] = defaultdict(list)
    for run in runs:
        by_policy[run.policy_name].append(run)
    summary_rows, summary_levels = [], []
    for name in sorted(by_policy, key=str.lower):
        items = by_policy[name]
        good, relevant = _rate(items)
        failed = sum(1 for r in items if r.status in _FAILED)
        warnings = sum(1 for r in items if r.status in _WARN)
        durations = [(r.finished_at - r.started_at).total_seconds() for r in items if r.finished_at]
        summary_rows.append([
            name, str(len(items)), str(sum(1 for r in items if r.status in _OK)), str(warnings), str(failed),
            str(sum(1 for r in items if r.status in _CANCELLED)), pct(good, relevant),
            fmt_duration(sum(durations) / len(durations) if durations else None),
        ])
        summary_levels.append("bad" if failed else ("warn" if warnings else "ok"))

    # Betroffene VMs eines problematischen Laufs: fehlgeschlagene Checkpoint-
    # Schritte ("Checkpoint erstellen: <VM>").
    problem_runs = [r for r in runs if r.status in _FAILED | _WARN]
    affected: dict[str, list[str]] = defaultdict(list)
    if problem_runs:
        for step in db.query(BackupRunStep).filter(
            BackupRunStep.run_id.in_([r.id for r in problem_runs]), BackupRunStep.status == "error",
        ):
            if ":" in step.label and step.label.lower().startswith("checkpoint"):
                affected[step.run_id].append(step.label.split(":", 1)[1].strip())
    problem_rows = [
        [fmt_dt(r.started_at), r.policy_name, _LABEL.get(r.status, str(r.status)), ", ".join(affected.get(r.id, [])) or "–",
         (r.error_message or "–")[:300]]
        for r in reversed(problem_runs)
    ]

    sections = [
        Section(
            "Übersicht je Policy",
            ["Policy", "Läufe", "Erfolgreich", "Mit Warnungen", "Fehlgeschlagen", "Abgebrochen", "Erfolgsquote", "Ø Dauer"],
            summary_rows, summary_levels, widths=[2.4, 0.7, 0.9, 1.0, 1.0, 0.9, 0.9, 1.0], empty_text="Keine Backup-Läufe im Zeitraum.",
            status_col=6,
        ),
        Section(
            "Fehlgeschlagene Läufe und Läufe mit Warnungen",
            ["Start", "Policy", "Status", "Betroffene VMs", "Meldung"], problem_rows,
            ["bad" if r.status in _FAILED else "warn" for r in reversed(problem_runs)], widths=[1.1, 1.4, 1.0, 2.0, 4.0],
            empty_text="Keine Probleme im Zeitraum.", status_col=2,
        ),
    ]
    detail_rows = [
        [fmt_dt(r.started_at), r.policy_name, _LABEL.get(r.status, str(r.status)), ", ".join((r.targets or [])[:6]),
         fmt_duration((r.finished_at - r.started_at).total_seconds() if r.finished_at else None), r.requested_by or "System"]
        for r in reversed(runs)
    ]
    if params.get("detail"):
        sections.append(Section(
            "Alle Läufe", ["Start", "Policy", "Status", "Ziele", "Dauer", "Initiator"], detail_rows,
            ["bad" if r.status in _FAILED else "warn" if r.status in _WARN else "ok" for r in reversed(runs)],
            widths=[1.1, 1.5, 1.0, 3.6, 0.8, 1.0], status_col=2,
        ))

    good, relevant = _rate(runs)
    prev_good, prev_relevant = _rate(previous)
    failed = sum(1 for r in runs if r.status in _FAILED)
    warnings = sum(1 for r in runs if r.status in _WARN)
    rate = good / relevant * 100 if relevant else None
    prev_rate = prev_good / prev_relevant * 100 if prev_relevant else None
    kpis = [
        Kpi("Läufe", str(len(runs))),
        Kpi("Erfolgsquote", f"{fmt_num(rate)} %" if rate is not None else "–", "ok" if rate == 100 else ("warn" if rate and rate >= 95 else ("bad" if rate is not None else "neutral"))),
        Kpi("Fehlgeschlagen", str(failed), "bad" if failed else "ok"),
        Kpi("Mit Warnungen", str(warnings), "warn" if warnings else "ok"),
    ]
    if prev_rate is None:
        comparison = f"Vorzeitraum ({period.previous_label}): keine Läufe."
    else:
        delta = (rate or 0) - prev_rate
        comparison = (
            f"Vorzeitraum ({period.previous_label}): {len(previous)} Läufe, Erfolgsquote {fmt_num(prev_rate)} % "
            f"({'+' if delta >= 0 else '−'}{fmt_num(abs(delta))} Prozentpunkte)."
        )

    scope = []
    if policy_ids:
        scope.append("Policy " + ", ".join(sorted(p.name for p in db.query(BackupPolicy).filter(BackupPolicy.id.in_(policy_ids)))))
    if group_ids:
        scope.append("Protection Group " + ", ".join(sorted(g.name for g in db.query(ResourceGroup).filter(ResourceGroup.id.in_(group_ids)))))
    return ReportContent(
        title="Backup-Erfolg", subtitle=f"Zeitraum {period.label}" + (f" · {' · '.join(scope)}" if scope else ""),
        kpis=kpis, comparison=comparison, findings=failed + warnings,
        findings_text=f"{failed} fehlgeschlagen, {warnings} mit Warnungen" if failed + warnings else "Alle Läufe erfolgreich",
        sections=sections,
        csv_columns=["Start", "Policy", "Status", "Ziele", "Dauer", "Initiator"], csv_rows=detail_rows,
    )
