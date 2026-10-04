"""Report "Restore-Nachweis" (Backlog #84 Stufe 2): alle Wiederherstellungen
im Zeitraum -- Disk-Restore (ersetzen/anhaengen), VM-Neuerstellung (auch
Side-by-side) und Datei-Restore -- mit Wer, Wann, welche VM, welcher
Backup-Stand, Dauer und Ergebnis. Fuer Audits ("wurde die Wiederherstellung
geuebt / funktioniert sie?"), mit Vergleich zum Vorzeitraum.

Auswahl (params): period (+ period_from/period_to), kinds (disk, recreate,
file; Standard alle), vm_names, only_failed."""

from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.reports.base import Kpi, ReportContent, Section, aware, fmt_dt, fmt_duration, resolve_period
from app.models.backup_run import BackupRun, BackupRunSnapshot
from app.models.file_restore_run import FileRestoreRun
from app.models.restore_run import RestoreRun
from app.models.vm_recreate_run import VmRecreateRun

KIND_LABEL = {"disk": "Disk-Restore", "recreate": "VM-Neuerstellung", "file": "Datei-Restore"}
_STATUS = {"succeeded": ("Erfolgreich", "ok"), "failed": ("Fehlgeschlagen", "bad"), "cleaned_up": ("Fehlgeschlagen, aufgeräumt", "bad"),
           "running": ("Läuft", "warn")}
_MODE = {"replace": "ersetzen", "add": "anhängen"}


def _status_value(run) -> str:
    raw = run.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _collect(db: Session, start: datetime, end: datetime, kinds: set[str], vm_names: set[str]) -> list[dict]:
    rows: list[dict] = []
    snap_ids: set[str] = set()
    run_ids: set[str] = set()

    def keep(name: str) -> bool:
        return not vm_names or name in vm_names

    if "disk" in kinds:
        for r in db.query(RestoreRun).filter(RestoreRun.started_at >= start, RestoreRun.started_at < end):
            if keep(r.vm_name):
                mode = _MODE.get(str(getattr(r.mode, "value", r.mode)), str(r.mode))
                rows.append({"run": r, "kind": "disk", "detail": f"Disk {mode}: {(r.source_vhd_path or '').split(chr(92))[-1]}",
                             "vm": r.vm_name, "snap": r.source_snapshot_id})
                snap_ids.add(r.source_snapshot_id)
    if "recreate" in kinds:
        for r in db.query(VmRecreateRun).filter(VmRecreateRun.started_at >= start, VmRecreateRun.started_at < end):
            target = r.target_vm_name or r.vm_name
            if keep(r.vm_name) or keep(target):
                detail = "Neuerstellung" if target == r.vm_name else f"Side-by-side als {target}"
                if r.disconnect_network:
                    detail += ", Netzwerk getrennt"
                rows.append({"run": r, "kind": "recreate", "detail": detail, "vm": r.vm_name, "backup_run": r.source_run_id})
                run_ids.add(r.source_run_id)
    if "file" in kinds:
        for r in db.query(FileRestoreRun).filter(FileRestoreRun.started_at >= start, FileRestoreRun.started_at < end):
            if keep(r.vm_name):
                rows.append({"run": r, "kind": "file", "vm": r.vm_name, "snap": r.source_snapshot_id,
                             "detail": "vom SnapMirror-Ziel" if r.used_secondary else "vom Primärsystem"})
                snap_ids.add(r.source_snapshot_id)

    snaps = {s.id: s.created_at for s in db.query(BackupRunSnapshot).filter(BackupRunSnapshot.id.in_(snap_ids))} if snap_ids else {}
    runs = {b.id: b.started_at for b in db.query(BackupRun).filter(BackupRun.id.in_(run_ids))} if run_ids else {}
    for row in rows:
        row["backup_at"] = snaps.get(row.get("snap")) if row.get("snap") else runs.get(row.get("backup_run"))
    rows.sort(key=lambda r: aware(r["run"].started_at), reverse=True)
    return rows


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    period = resolve_period(params, now)
    kinds = set(params.get("kinds") or KIND_LABEL)
    vm_names = set(params.get("vm_names") or [])
    only_failed = bool(params.get("only_failed"))
    rows = _collect(db, period.start, period.end, kinds, vm_names)
    previous = _collect(db, period.previous_start, period.previous_end, kinds, vm_names)

    table, levels = [], []
    for row in rows:
        run = row["run"]
        label, level = _STATUS.get(_status_value(run), (_status_value(run), "neutral"))
        if only_failed and level != "bad":
            continue
        started, finished = aware(run.started_at), aware(run.finished_at)
        table.append([
            fmt_dt(started), KIND_LABEL[row["kind"]], row["vm"], row["detail"], fmt_dt(row["backup_at"]),
            run.requested_by or "System", fmt_duration((finished - started).total_seconds() if finished else None), label,
            (run.error_message or "–")[:300],
        ])
        levels.append(level)

    def counts(items: list[dict]) -> tuple[int, int, int]:
        states = [_status_value(r["run"]) for r in items]
        return len(items), states.count("succeeded"), sum(1 for s in states if s in ("failed", "cleaned_up"))

    total, ok, failed = counts(rows)
    prev_total, prev_ok, prev_failed = counts(previous)
    by_kind = Counter(r["kind"] for r in rows)
    vms = {r["vm"] for r in rows if _status_value(r["run"]) == "succeeded"}
    kpis = [
        Kpi("Wiederherstellungen", str(total)),
        Kpi("erfolgreich", str(ok), "ok" if ok else "neutral"),
        Kpi("fehlgeschlagen", str(failed), "bad" if failed else "ok"),
        Kpi("VMs erfolgreich wiederhergestellt", str(len(vms))),
    ]
    comparison = (
        "Nach Art: " + ", ".join(f"{KIND_LABEL[k]} {by_kind.get(k, 0)}" for k in KIND_LABEL if k in kinds)
        + f". Vorzeitraum ({period.previous_label}): {prev_total} Wiederherstellung(en), {prev_ok} erfolgreich, {prev_failed} fehlgeschlagen."
    )
    scope = []
    if kinds != set(KIND_LABEL):
        scope.append(", ".join(KIND_LABEL[k] for k in KIND_LABEL if k in kinds))
    if vm_names:
        scope.append(f"{len(vm_names)} ausgewählte VM(s)")
    columns = ["Start", "Art", "VM", "Details", "Backup-Stand", "Initiator", "Dauer", "Status", "Meldung"]
    return ReportContent(
        title="Restore-Nachweis", subtitle=f"Zeitraum {period.label}" + (f" · {' · '.join(scope)}" if scope else ""),
        kpis=kpis, comparison=comparison, findings=failed,
        findings_text=f"{failed} von {total} Wiederherstellung(en) fehlgeschlagen" if failed else f"{total} Wiederherstellung(en), alle erfolgreich",
        sections=[Section(
            "Wiederherstellungen", columns, table, levels, widths=[1.1, 1.1, 1.5, 2.0, 1.1, 1.0, 0.8, 1.0, 2.6], status_col=7,
            empty_text="Keine fehlgeschlagenen Wiederherstellungen im Zeitraum." if only_failed else "Keine Wiederherstellungen im Zeitraum.",
            note="Backup-Stand = Zeitpunkt des Backups, aus dem wiederhergestellt wurde.",
        )],
        csv_columns=columns, csv_rows=table,
    )
