"""E-Mail-Versand fuer das Alerting-Feature (Settings > E-Mail). Nutzt
smtplib gegen einen vom Admin konfigurierten SMTP-Relay/Smart-Host --
bewusst KEIN direkter Versand per MX-Lookup (siehe Diskussion mit dem
Nutzer): ohne Sende-Reputation/SPF/DKIM/rDNS wuerden Mails an Gmail/M365
etc. praktisch immer im Spam landen oder abgelehnt werden.

Jede send_*-Funktion fasst Fehler beim Versand selbst ab (best-effort,
analog zum bestehenden Discovery-nach-Restore-Muster) und schreibt sie als
SystemLogEvent -- ein SMTP-Ausfall soll niemals einen Backup-/Restore-Lauf
zum Scheitern bringen."""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.models.email_config import EmailConfig
from app.models.system_log import SystemLogEvent


def _log(db: Session, message: str, level: str = "INFO") -> None:
    print(f"[email] {datetime.now(timezone.utc).isoformat()} {message}", flush=True)
    db.add(SystemLogEvent(level=level, source="email", message=message))
    db.commit()


def get_email_config(db: Session) -> EmailConfig | None:
    return db.query(EmailConfig).first()


def send_raw_email(config: EmailConfig, recipients: list[str], subject: str, html_body: str, text_body: str) -> None:
    """Baut die Mail und liefert sie an den konfigurierten SMTP-Relay aus.
    Wirft bei jedem Fehler (Verbindung, Auth, Empfaenger abgelehnt) --
    Aufrufer entscheiden je nach Kontext, ob best-effort abgefangen wird
    (Alerts) oder der Fehler direkt an den Nutzer zurueckgeht (Test-Mail)."""
    if not recipients:
        raise ValueError("Keine Empfaenger konfiguriert")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{config.from_name} <{config.from_address}>" if config.from_name else config.from_address
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    password = decrypt_secret(config.encrypted_password) if config.encrypted_password else None

    if config.smtp_encryption == "ssl":
        smtp = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15)
    else:
        smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15)
    try:
        smtp.ehlo()
        if config.smtp_encryption == "starttls":
            smtp.starttls()
            smtp.ehlo()
        if config.smtp_username:
            smtp.login(config.smtp_username, password or "")
        smtp.sendmail(config.from_address, recipients, msg.as_string())
    finally:
        smtp.quit()


def send_test_email(db: Session, config: EmailConfig, recipient: str) -> None:
    """Wird direkt ueber den 'Test-Mail senden'-Button aufgerufen -- Fehler
    sollen hier bewusst NICHT verschluckt werden, damit der Admin beim
    Einrichten sofort sieht, ob Host/Port/Zugangsdaten stimmen."""
    send_raw_email(
        config,
        [recipient],
        subject="Test-E-Mail -- Hyper-V NetApp Backup",
        html_body="<p>Das ist eine Test-E-Mail aus dem Hyper-V NetApp Backup Tool. "
        "Wenn du diese Nachricht erhaeltst, ist die SMTP-Konfiguration korrekt.</p>",
        text_body="Das ist eine Test-E-Mail aus dem Hyper-V NetApp Backup Tool. "
        "Wenn du diese Nachricht erhaeltst, ist die SMTP-Konfiguration korrekt.",
    )


def _send_best_effort(db: Session, config: EmailConfig, subject: str, html_body: str, text_body: str, context: str) -> None:
    recipients = config.recipient_list()
    if not recipients:
        _log(db, f"E-Mail-Alert '{context}' uebersprungen: keine Empfaenger konfiguriert", level="WARNING")
        return
    try:
        send_raw_email(config, recipients, subject, html_body, text_body)
        _log(db, f"E-Mail-Alert '{context}' an {len(recipients)} Empfaenger verschickt")
    except Exception as exc:
        _log(db, f"E-Mail-Alert '{context}' konnte nicht verschickt werden: {exc}", level="ERROR")


def notify_backup_failure(
    db: Session, policy_name: str, run_id: str, error_message: str | None, targets: list[str], alert_enabled: bool,
) -> None:
    """alert_enabled kommt von BackupPolicy.email_alert_on_failure -- pro
    Policy statt global schaltbar (Nutzer-Vorgabe: 'Alerting soll in der
    Policy aktiviert werden koennen, als weitere Option bei der
    Definition'). Der globale EmailConfig.enabled-Schalter bleibt zusaetzlich
    die Voraussetzung dafuer, dass ueberhaupt SMTP konfiguriert/aktiv ist."""
    if not alert_enabled:
        return
    config = get_email_config(db)
    if config is None or not config.enabled:
        return
    subject = f"[Hyper-V NetApp Backup] Backup fehlgeschlagen: {policy_name}"
    targets_str = ", ".join(targets) if targets else "-"
    text = (
        f"Der Backup-Lauf fuer Policy '{policy_name}' ist fehlgeschlagen.\n\n"
        f"Ziele: {targets_str}\n"
        f"Fehler: {error_message or '(keine Details)'}\n"
        f"Lauf-ID: {run_id}\n"
    )
    html = (
        f"<p>Der Backup-Lauf fuer Policy <b>{policy_name}</b> ist fehlgeschlagen.</p>"
        f"<p><b>Ziele:</b> {targets_str}<br>"
        f"<b>Fehler:</b> {error_message or '(keine Details)'}<br>"
        f"<b>Lauf-ID:</b> {run_id}</p>"
    )
    _send_best_effort(db, config, subject, html, text, context=f"Backup fehlgeschlagen ({policy_name})")


def notify_restore_failure(db: Session, kind: str, vm_name: str, run_id: str, error_message: str | None) -> None:
    """kind: Klartext-Label fuer die Betreffzeile, z.B. 'Restore',
    'VM-Neuerstellung', 'Datei-Restore'."""
    config = get_email_config(db)
    if config is None or not config.enabled or not config.notify_on_restore_failure:
        return
    subject = f"[Hyper-V NetApp Backup] {kind} fehlgeschlagen: {vm_name}"
    text = (
        f"Der {kind}-Lauf fuer VM '{vm_name}' ist fehlgeschlagen.\n\n"
        f"Fehler: {error_message or '(keine Details)'}\n"
        f"Lauf-ID: {run_id}\n"
    )
    html = (
        f"<p>Der {kind}-Lauf fuer VM <b>{vm_name}</b> ist fehlgeschlagen.</p>"
        f"<p><b>Fehler:</b> {error_message or '(keine Details)'}<br>"
        f"<b>Lauf-ID:</b> {run_id}</p>"
    )
    _send_best_effort(db, config, subject, html, text, context=f"{kind} fehlgeschlagen ({vm_name})")


def send_daily_summary(db: Session, config: EmailConfig, stats: "DailySummaryStats") -> None:
    subject = f"[Hyper-V NetApp Backup] Tageszusammenfassung {stats.date_label} ({stats.total_failed} fehlgeschlagen)"
    text_lines = [f"Zusammenfassung der letzten 24 Stunden ({stats.date_label}):", ""]
    html_rows = []
    for row in stats.rows:
        text_lines.append(f"- {row.label}: {row.total} gesamt, {row.succeeded} erfolgreich, {row.failed} fehlgeschlagen")
        html_rows.append(
            f"<tr><td>{row.label}</td><td align='right'>{row.total}</td>"
            f"<td align='right'>{row.succeeded}</td><td align='right'>{row.failed}</td></tr>"
        )
    if stats.failures:
        text_lines.append("")
        text_lines.append("Fehlgeschlagene Laeufe:")
        for f in stats.failures:
            text_lines.append(f"- [{f.kind}] {f.name}: {f.error or '(keine Details)'}")
    failures_html = ""
    if stats.failures:
        items = "".join(f"<li><b>[{f.kind}] {f.name}:</b> {f.error or '(keine Details)'}</li>" for f in stats.failures)
        failures_html = f"<p><b>Fehlgeschlagene Laeufe:</b></p><ul>{items}</ul>"
    html = (
        f"<p>Zusammenfassung der letzten 24 Stunden ({stats.date_label}):</p>"
        f"<table border='1' cellpadding='4' cellspacing='0'>"
        f"<tr><th>Typ</th><th>Gesamt</th><th>Erfolgreich</th><th>Fehlgeschlagen</th></tr>"
        f"{''.join(html_rows)}</table>{failures_html}"
    )
    _send_best_effort(db, config, subject, html, "\n".join(text_lines), context="Tageszusammenfassung")


class DailySummaryRow:
    def __init__(self, label: str, total: int, succeeded: int, failed: int) -> None:
        self.label = label
        self.total = total
        self.succeeded = succeeded
        self.failed = failed


class DailySummaryFailure:
    def __init__(self, kind: str, name: str, error: str | None) -> None:
        self.kind = kind
        self.name = name
        self.error = error


class DailySummaryStats:
    def __init__(self, date_label: str, rows: list[DailySummaryRow], failures: list[DailySummaryFailure]) -> None:
        self.date_label = date_label
        self.rows = rows
        self.failures = failures
        self.total_failed = sum(r.failed for r in rows)
