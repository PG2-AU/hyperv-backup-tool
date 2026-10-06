"""Report "Audit-Trail" (Backlog #84 Stufe 2): wer hat im Zeitraum was
angelegt, geaendert, geloescht, verschoben oder wiederhergestellt.

Quellen:
- Aenderungsprotokoll (AuditEvent, app.core.audit): jede aendernde Anfrage
  eines Benutzers mit Ergebnis -- erst ab Einfuehrung des Protokolls
  vorhanden. Davor liegende Eintraege im System-Log, die einen Benutzer
  nennen ("... (durch X)"), fuellen die Zeit davor auf.
- Ablaeufe (Backup, Restore, VM/CSV/SMB3 anlegen/loeschen/verschieben ...):
  mit Endergebnis; geplante Backups nur auf Wunsch.
- Anmeldungen aus dem System-Log (erfolgreich/fehlgeschlagen).

Auswahl (params): period (+ period_from/period_to), usernames, include_runs
(ja), include_scheduled (nein), include_logins (ja)."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.reports.base import Kpi, ReportContent, Section, aware, fmt_dt, resolve_period
from app.models.audit import AuditEvent
from app.models.system_log import SystemLogEvent

_RUN_STATUS = {"succeeded": ("Erfolgreich", "ok"), "warning": ("Mit Warnungen", "warn"), "failed": ("Fehlgeschlagen", "bad"),
               "cleaned_up": ("Fehlgeschlagen", "bad"), "cancelled": ("Abgebrochen", "warn"), "running": ("Läuft", "neutral")}
_LOG_SOURCE = {"storage": "Storage", "sites": "Standort", "users": "Benutzer", "vm-console": "Remote-Sitzung", "vm-power": "VM",
               "vm-move": "VM", "vm-create": "VM", "vm-delete": "VM", "vm-settings": "VM", "config-transfer": "Konfiguration", "reports": "Report"}


def _result(code: int) -> tuple[str, str]:
    if code < 400:
        return "OK", "ok"
    if code in (401, 403):
        return "Verweigert", "warn"
    if code < 500:
        return f"Abgelehnt ({code})", "warn"
    return f"Fehler ({code})", "bad"


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    from app.api.routes.activities import KINDS, _status

    period = resolve_period(params, now)
    users = {u.strip().lower() for u in (params.get("usernames") or []) if u.strip()}
    include_runs = params.get("include_runs", True)
    include_scheduled = bool(params.get("include_scheduled"))
    include_logins = params.get("include_logins", True)

    def user_ok(name: str | None) -> bool:
        return not users or (name or "").lower() in users

    events = [
        e for e in db.query(AuditEvent)
        .filter(AuditEvent.timestamp >= period.start, AuditEvent.timestamp < period.end)
        .order_by(AuditEvent.timestamp.desc())
        if user_ok(e.username)
    ]
    first_audit = db.query(AuditEvent.timestamp).order_by(AuditEvent.timestamp).first()
    first_audit_at = aware(first_audit[0]) if first_audit else None

    change_rows: list[tuple[datetime, list[str], str]] = []
    denied = failed = 0
    for e in events:
        label, level = _result(e.status_code)
        denied += level == "warn"
        failed += level == "bad"
        change_rows.append((aware(e.timestamp), [fmt_dt(e.timestamp), e.username, e.area, e.action, e.target or "–", label, e.client_ip or "–"], level))

    # Zeit vor dem Aenderungsprotokoll: System-Log-Eintraege mit "(durch X)".
    # (kleiner Puffer: der erste protokollierte Aufruf schreibt oft selbst noch einen System-Log-Eintrag)
    legacy_end = min(period.end, first_audit_at - timedelta(seconds=10)) if first_audit_at else period.end
    if period.start < legacy_end:
        for log in (
            db.query(SystemLogEvent)
            .filter(SystemLogEvent.timestamp >= period.start, SystemLogEvent.timestamp < legacy_end,
                    SystemLogEvent.source != "auth", SystemLogEvent.message.like("%(durch %"))
        ):
            who = log.message.rsplit("(durch ", 1)[-1].rstrip(")").strip()
            if not user_ok(who):
                continue
            level = "bad" if log.level == "ERROR" else "ok"
            failed += level == "bad"
            change_rows.append((aware(log.timestamp), [
                fmt_dt(log.timestamp), who, _LOG_SOURCE.get(log.source, log.source),
                log.message.rsplit(" (durch ", 1)[0][:300], "–", "Fehler" if level == "bad" else "OK", "–",
            ], level))
    change_rows.sort(key=lambda r: r[0], reverse=True)

    sections = [Section(
        "Änderungen und Aktionen", ["Zeitpunkt", "Benutzer", "Bereich", "Aktion", "Objekt", "Ergebnis", "Adresse"],
        [r[1] for r in change_rows], [r[2] for r in change_rows], widths=[1.1, 1.2, 1.2, 2.2, 2.6, 0.9, 0.9], status_col=5,
        empty_text="Keine Änderungen im Zeitraum.",
        note=(
            f"Vollständiges Änderungsprotokoll seit {fmt_dt(first_audit_at)}"
            + ("; davor nur die Einträge des System-Logs, die einen Benutzer nennen." if first_audit_at and first_audit_at > period.start else ".")
        ) if first_audit_at else "Das Änderungsprotokoll enthält noch keine Einträge; gezeigt werden System-Log-Einträge, die einen Benutzer nennen.",
    )]
    csv_rows = [["Änderung"] + r[1] for r in change_rows]

    run_count = run_failed = 0
    if include_runs:
        run_rows: list[tuple[datetime, list[str], str]] = []
        for name, kind in KINDS.items():
            model = kind.model
            for run in db.query(model).filter(model.started_at >= period.start, model.started_at < period.end):
                initiator = getattr(run, "requested_by", None)
                if name == "backup" and not initiator and not include_scheduled:
                    continue
                if not user_ok(initiator or "System"):
                    continue
                label, level = _RUN_STATUS.get(_status(run), (_status(run), "neutral"))
                run_failed += level == "bad"
                started, finished = aware(run.started_at), aware(run.finished_at)
                # Ohne Initiator: Backup = Zeitplan; sonst ein Altlauf von vor
                # der Initiator-Erfassung (Backlog #83)
                who = initiator or ("System (Zeitplan)" if name == "backup" else "unbekannt")
                run_rows.append((started, [
                    fmt_dt(started), who, kind.task(run), kind.target(run) or "–", label,
                    fmt_dt(finished), (getattr(run, "error_message", None) or "–")[:250],
                ], level))
        run_rows.sort(key=lambda r: r[0], reverse=True)
        run_count = len(run_rows)
        sections.append(Section(
            "Abläufe", ["Start", "Initiator", "Aufgabe", "Ziel", "Ergebnis", "Ende", "Meldung"],
            [r[1] for r in run_rows], [r[2] for r in run_rows], widths=[1.1, 1.2, 1.8, 2.0, 1.0, 1.1, 2.6], status_col=4,
            empty_text="Keine Abläufe im Zeitraum.",
            note=None if include_scheduled else "Geplante Backups sind nicht enthalten (siehe Report Backup-Erfolg).",
        ))
        csv_rows += [["Ablauf"] + r[1] for r in run_rows]

    logins_failed = 0
    if include_logins:
        login_rows, login_levels = [], []
        for log in (
            db.query(SystemLogEvent)
            .filter(SystemLogEvent.timestamp >= period.start, SystemLogEvent.timestamp < period.end, SystemLogEvent.source == "auth")
            .order_by(SystemLogEvent.timestamp.desc())
        ):
            who = log.message.rsplit(":", 1)[-1].strip()
            if not user_ok(who):
                continue
            bad = "fehlgeschlagen" in log.message.lower() or log.level != "INFO"
            logins_failed += bad
            login_rows.append([fmt_dt(log.timestamp), who, log.message[:300]])
            login_levels.append("warn" if bad else "ok")
        sections.append(Section(
            "Anmeldungen", ["Zeitpunkt", "Benutzer", "Meldung"], login_rows, login_levels, widths=[1.1, 1.5, 6.0], status_col=2,
            empty_text="Keine Anmeldungen im Zeitraum.",
        ))
        csv_rows += [["Anmeldung", r[0], r[1], "", r[2], "", "", ""] for r in login_rows]

    active_users = {r[1][1] for r in change_rows}
    kpis = [
        Kpi("Änderungen/Aktionen", str(len(change_rows))),
        Kpi("aktive Benutzer", str(len(active_users))),
        Kpi("verweigert/abgelehnt", str(denied), "warn" if denied else "ok"),
    ]
    if include_runs:
        kpis.append(Kpi("Abläufe (fehlgeschlagen)", f"{run_count} ({run_failed})", "bad" if run_failed else "neutral"))
    if include_logins:
        kpis.append(Kpi("fehlgeschlagene Anmeldungen", str(logins_failed), "warn" if logins_failed else "ok"))
    findings = denied + failed + logins_failed
    parts = []
    if denied:
        parts.append(f"{denied} verweigerte/abgelehnte Aktion(en)")
    if failed:
        parts.append(f"{failed} fehlgeschlagene Änderung(en)")
    if logins_failed:
        parts.append(f"{logins_failed} fehlgeschlagene Anmeldung(en)")
    subtitle = f"Zeitraum {period.label}" + (f" · Benutzer {', '.join(sorted(users))}" if users else "")
    return ReportContent(
        title="Audit-Trail", subtitle=subtitle, kpis=kpis, findings=findings,
        findings_text=", ".join(parts) if parts else f"{len(change_rows)} Änderung(en), keine Auffälligkeiten",
        sections=sections, csv_columns=["Art", "Zeitpunkt", "Benutzer", "Bereich/Aufgabe", "Aktion/Ziel", "Objekt/Ergebnis", "Ergebnis/Ende",
                                        "Adresse/Meldung"],
        csv_rows=csv_rows,
    )
