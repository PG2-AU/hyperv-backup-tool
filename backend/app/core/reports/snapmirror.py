"""Report "SnapMirror / DR" (Backlog #84 Stufe 2): Zustand aller SnapMirror-
Beziehungen (Zustand, gesund, Lag, letzte Uebertragung, Fehler) und je
gesichertem Quell-Volume die Backup-Kopien auf dem Ziel (Anzahl, aelteste,
neueste) -- "koennen wir am DR-Standort wiederherstellen, und wie aktuell?".
Grundlage: letzte NetApp-Discovery und der Snapshot-Abgleich der App, keine
Live-Abfrage.

Auswahl (params): netapp_cluster_ids, lag_hours (Standard: Schwellwert aus
Settings > Alarms), max_copy_age_hours (juengste Kopie aelter -> Warnung,
Standard 26), include_copies (Standard ja), only_findings."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, selectinload

from app.core.reports.base import Kpi, ReportContent, Section, aware, fmt_age, fmt_bytes, fmt_dt
from app.models.alert import AlertConfig
from app.models.backup_run import BackupRunSnapshot
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSnapMirrorRelationship


def _fmt_lag(minutes: int | None) -> str:
    if minutes is None:
        return "–"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes // 1440} Tage {minutes % 1440 // 60} h"


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    from app.core.scheduler import _parse_lag_minutes

    config = db.query(AlertConfig).first()
    lag_hours = int(params.get("lag_hours") or (config.snapmirror_lag_threshold_hours if config else 4))
    max_copy_age = timedelta(hours=int(params.get("max_copy_age_hours") or 26))
    cluster_ids = set(params.get("netapp_cluster_ids") or [])
    include_copies = params.get("include_copies", True)
    only_findings = bool(params.get("only_findings"))
    cluster_names = {c.id: c.name for c in db.query(NetAppCluster)}

    rel_rows, rel_levels = [], []
    unhealthy = lagging = 0
    relationships = [r for r in db.query(NetAppSnapMirrorRelationship).all() if not cluster_ids or r.cluster_id in cluster_ids]
    for r in sorted(relationships, key=lambda r: (r.source_path or "").lower()):
        lag = _parse_lag_minutes(r.lag_time)
        problems = []
        if not r.healthy:
            problems.append("nicht gesund")
        if r.last_transfer_error:
            problems.append(r.last_transfer_error)
        level = "bad" if problems else ("warn" if lag is not None and lag >= lag_hours * 60 else "ok")
        unhealthy += level == "bad"
        lagging += level == "warn"
        if only_findings and level == "ok":
            continue
        rel_rows.append([
            r.source_path or "–", r.destination_path or "–", r.destination_cluster_name or cluster_names.get(r.cluster_id, "–"),
            r.state or "–", _fmt_lag(lag), r.policy_name or "–", r.schedule_name or "–", fmt_bytes(r.last_transfer_size_bytes),
            {"bad": "Fehler", "warn": "Lag zu hoch", "ok": "OK"}[level], "; ".join(problems)[:300] or "–",
        ])
        rel_levels.append(level)

    sections = [Section(
        "SnapMirror-Beziehungen", ["Quelle", "Ziel", "Zielsystem", "Zustand", "Lag", "Policy", "Schedule", "Letzte Übertragung",
                                   "Status", "Meldung"],
        rel_rows, rel_levels, widths=[1.8, 1.8, 1.1, 0.9, 0.9, 1.1, 0.9, 0.9, 0.8, 1.8], status_col=8,
        empty_text="Keine Auffälligkeiten." if only_findings else "Keine SnapMirror-Beziehungen erkannt.",
        note=f"Lag-Schwellwert {lag_hours} h. Stand: letzte NetApp-Discovery.",
    )]

    stale_copies = 0
    copy_rows: list[list[str]] = []
    if include_copies:
        copies: dict[tuple[str, str, str], list] = defaultdict(list)
        targets: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for snap in db.query(BackupRunSnapshot).options(selectinload(BackupRunSnapshot.destinations)).all():
            if cluster_ids and snap.netapp_cluster_id not in cluster_ids:
                continue
            present = [d for d in snap.destinations if d.present]
            key = (snap.netapp_cluster_name or "–", snap.svm_name or "–", snap.volume_name or "–")
            if not snap.destinations:
                continue
            for d in present:
                targets[key].add(f"{d.destination_svm_name}:{d.destination_volume_name}")
            if present:
                copies[key].append(aware(snap.created_at))
            else:
                copies.setdefault(key, [])
        copy_levels = []
        for key in sorted(copies, key=lambda k: (k[1].lower(), k[2].lower())):
            times = sorted(copies[key])
            newest = times[-1] if times else None
            level = "bad" if not times else ("warn" if now - newest > max_copy_age else "ok")
            stale_copies += level != "ok"
            if only_findings and level == "ok":
                continue
            copy_rows.append([
                f"{key[1]}:{key[2]}", ", ".join(sorted(targets[key])) or "–", str(len(times)), fmt_dt(times[0] if times else None),
                fmt_dt(newest), fmt_age(newest, now), {"bad": "Keine Kopie", "warn": "Veraltet", "ok": "OK"}[level],
            ])
            copy_levels.append(level)
        sections.append(Section(
            "Backup-Kopien auf dem SnapMirror-Ziel je Volume",
            ["Quell-Volume", "Ziel-Volume", "Kopien", "älteste", "neueste", "Alter", "Status"], copy_rows, copy_levels,
            widths=[2.0, 2.0, 0.6, 1.1, 1.1, 0.7, 0.8],
            empty_text="Keine Auffälligkeiten." if only_findings else "Keine Backups mit SnapMirror-Ziel.",
            note=f"Nur Backups der App mit SnapMirror-Update; Veraltet = jüngste Kopie älter als {int(max_copy_age.total_seconds() // 3600)} h.",
        ))

    kpis = [
        Kpi("Beziehungen", str(len(relationships))),
        Kpi("mit Fehler", str(unhealthy), "bad" if unhealthy else "ok"),
        Kpi(f"Lag > {lag_hours} h", str(lagging), "warn" if lagging else "ok"),
    ]
    if include_copies:
        kpis.append(Kpi("Volumes ohne aktuelle Kopie", str(stale_copies), "warn" if stale_copies else "ok"))
    findings = unhealthy + lagging + stale_copies
    parts = []
    if unhealthy:
        parts.append(f"{unhealthy} Beziehung(en) mit Fehler")
    if lagging:
        parts.append(f"{lagging} mit zu hohem Lag")
    if stale_copies:
        parts.append(f"{stale_copies} Volume(s) ohne aktuelle Kopie")
    subtitle = f"Stand {fmt_dt(now)}" + (
        " · System " + ", ".join(sorted(cluster_names.get(i, i) for i in cluster_ids)) if cluster_ids else ""
    )
    return ReportContent(
        title="SnapMirror / DR", subtitle=subtitle, kpis=kpis, findings=findings,
        findings_text=", ".join(parts) if parts else "Alle Beziehungen gesund und aktuell",
        sections=sections,
        csv_columns=["Bereich", "Quelle", "Ziel", "Zielsystem/Kopien", "Zustand/älteste", "Lag/neueste", "Status"],
        csv_rows=[["Beziehung", r[0], r[1], r[2], r[3], r[4], r[8]] for r in rel_rows]
        + [["Kopien", r[0], r[1], r[2], r[3], r[4], r[6]] for r in copy_rows],
    )
