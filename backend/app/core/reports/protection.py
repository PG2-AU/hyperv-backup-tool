"""Report "Schutzstatus" (Backlog #84/#82): je VM, CSV und SMB3-Freigabe die
Protection Group(s), Policies, letztes erfolgreiches Backup primaer und
sekundaer und ob es zu alt ist. "Geschuetzt" kommt aus derselben Logik wie
Inventory > VMs/CSVs/SMB3 (list_vms/list_csvs/list_smb_shares), damit Report
und App dasselbe meinen.

Auswahl (params): cluster_ids, site_ids, resource_group_ids, max_age_hours
(ab wann ein Backup als ueberfaellig gilt, Standard 26), only_findings,
include_csv, include_smb."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, selectinload

from app.core.reports.base import Kpi, ReportContent, Section, fmt_age, fmt_dt, pct
from app.models.backup_run import BackupRunSnapshot, BackupRunVmConfig
from app.models.resource_group import ResourceGroup
from app.models.site import Site


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    from app.api.routes.vms import list_csvs, list_smb_shares, list_vms

    max_age = timedelta(hours=int(params.get("max_age_hours") or 26))
    only_findings = bool(params.get("only_findings"))
    cluster_ids = set(params.get("cluster_ids") or [])
    site_ids = set(params.get("site_ids") or [])
    group_ids = set(params.get("resource_group_ids") or [])
    group_names = {g.name for g in db.query(ResourceGroup).filter(ResourceGroup.id.in_(group_ids))} if group_ids else set()

    # Letztes erfolgreiches Backup je VM / CSV / Volume, primaer und sekundaer.
    not_captured = {(c.run_id, c.vm_name) for c in db.query(BackupRunVmConfig).filter(BackupRunVmConfig.not_captured.is_(True))}
    last_primary: dict[str, datetime] = {}
    last_secondary: dict[str, datetime] = {}

    def _note(store: dict, key: str, when: datetime) -> None:
        if key and (key not in store or when > store[key]):
            store[key] = when

    for row in db.query(BackupRunSnapshot).options(selectinload(BackupRunSnapshot.destinations)).all():
        secondary = any(d.present for d in row.destinations)
        if not (row.success or secondary):
            continue
        keys = [f"vm:{v}" for v in (row.vm_names or []) if (row.run_id, v) not in not_captured]
        keys += [f"csv:{c}" for c in (row.csv_names or [])]
        keys.append(f"vol:{row.svm_name}:{row.volume_name}")
        for key in keys:
            if row.success:
                _note(last_primary, key, row.created_at)
            if secondary:
                _note(last_secondary, key, row.created_at)

    def _status(protected: bool, key: str) -> tuple[str, str]:
        if not protected:
            return "Ungeschützt", "bad"
        last = last_primary.get(key) or last_secondary.get(key)
        if last is None:
            return "Noch kein Backup", "bad"
        if now - last > max_age:
            return "Überfällig", "warn"
        return "OK", "ok"

    def _in_scope(cluster_id: str | None, site_id: str | None, groups: list[str]) -> bool:
        if cluster_ids and cluster_id not in cluster_ids:
            return False
        if site_ids and site_id not in site_ids:
            return False
        return not group_names or bool(group_names & set(groups))

    # Schutzklassen (Backlog #86): Spalte nur, wenn ueberhaupt Klassen zugewiesen sind
    from app.core.protection_class import evaluate

    class_status = {(s.object_type, s.cluster_id, s.name): s for s in evaluate(db, now)}
    with_classes = any(s.class_id for s in class_status.values())
    class_violations = 0

    sections: list[Section] = []
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    csv_rows: list[list[str]] = []

    def _add_section(kind: str, title: str, objects: list[tuple[str, list[str], list[str], str, tuple]]) -> None:
        nonlocal class_violations
        rows, levels = [], []
        for name, groups, policies, key, lookup in objects:
            status, level = _status(bool(groups), key)
            cls = class_status.get(lookup)
            class_text = "–"
            if cls is not None and cls.class_id:
                class_text = cls.class_name
                if cls.status == "violation":
                    class_violations += 1
                    class_text += ": " + "; ".join(cls.violations)
                    if level == "ok":
                        status, level = "Klasse nicht erfüllt", "warn"
            counts[kind]["total"] += 1
            counts[kind][level] += 1
            if only_findings and level == "ok":
                continue
            primary, secondary = last_primary.get(key), last_secondary.get(key)
            row = [
                name, ", ".join(groups) or "–", ", ".join(policies) or "–", fmt_dt(primary), fmt_age(primary, now),
                fmt_dt(secondary), *([class_text[:300]] if with_classes else []), status,
            ]
            rows.append(row)
            levels.append(level)
            csv_rows.append([title, *row])
        order = {"bad": 0, "warn": 1, "ok": 2}
        paired = sorted(zip(rows, levels), key=lambda p: (order[p[1]], p[0][0].lower()))
        sections.append(Section(
            title=title,
            columns=["Name", "Protection Group", "Policies", "Letztes Backup (primär)", "Alter", "Letztes Backup (sekundär)",
                     *(["Schutzklasse"] if with_classes else []), "Status"],
            rows=[p[0] for p in paired], row_levels=[p[1] for p in paired],
            widths=[2.0, 1.4, 1.6, 1.3, 0.6, 1.3, 2.4, 1.1] if with_classes else [2.2, 1.6, 1.8, 1.4, 0.7, 1.4, 1.1],
            empty_text="Keine Auffälligkeiten." if only_findings else "Keine Objekte in der Auswahl.",
        ))

    vms = [
        v for v in list_vms(db, None)
        if _in_scope(v.cluster_id, v.host_site.id if v.host_site else None, v.resource_group_names)
    ]
    _add_section("vm", "Virtuelle Maschinen", [
        (f"{v.name} ({v.cluster})" if v.cluster else v.name, v.resource_group_names, v.policy_names, f"vm:{v.name}",
         ("vm", v.cluster_id, v.name)) for v in vms
    ])
    if params.get("include_csv", True):
        csvs = [c for c in list_csvs(db, None) if _in_scope(c.cluster_id, c.site.id if c.site else None, c.resource_group_names)]
        _add_section("csv", "Cluster Shared Volumes", [
            (c.name, c.resource_group_names, c.policy_names, f"csv:{c.name}", ("csv", c.cluster_id, c.name)) for c in csvs
        ])
    if params.get("include_smb", True):
        shares = [s for s in list_smb_shares(db, None) if _in_scope(s.cluster_id, None, s.resource_group_names)]
        _add_section("smb", "SMB3-Freigaben", [
            (f"\\\\{s.server}\\{s.share}", s.resource_group_names, s.policy_names, f"vol:{s.svm_name}:{s.volume_name}",
             ("smb_share", s.cluster_id, f"{s.server}|{s.share}"))
            for s in shares
        ])

    vm = counts["vm"]
    findings = sum(c["bad"] + c["warn"] for c in counts.values())
    kpis = [
        Kpi("VMs", str(vm["total"])),
        Kpi("davon geschützt + aktuell", f"{vm['ok']} ({pct(vm['ok'], vm['total'])})", "ok" if vm["ok"] == vm["total"] else "neutral"),
        Kpi("überfällig", str(vm["warn"]), "warn" if vm["warn"] else "ok"),
        Kpi("ungeschützt / ohne Backup", str(vm["bad"]), "bad" if vm["bad"] else "ok"),
    ]
    for kind, label in (("csv", "CSVs"), ("smb", "SMB3-Freigaben")):
        if counts[kind]["total"]:
            c = counts[kind]
            kpis.append(Kpi(f"{label} mit Auffälligkeit", f"{c['bad'] + c['warn']} von {c['total']}", "bad" if c["bad"] else ("warn" if c["warn"] else "ok")))

    if with_classes:
        kpis.append(Kpi("Schutzklasse nicht erfüllt", str(class_violations), "warn" if class_violations else "ok"))

    scope = []
    if cluster_ids:
        scope.append(f"{len(cluster_ids)} Cluster")
    if site_ids:
        names = [s.name for s in db.query(Site).filter(Site.id.in_(site_ids))]
        scope.append("Standort " + ", ".join(names))
    if group_names:
        scope.append("Protection Group " + ", ".join(sorted(group_names)))
    subtitle = f"Stand {fmt_dt(now)} · überfällig ab {int(max_age.total_seconds() // 3600)} h" + (f" · {' · '.join(scope)}" if scope else "")
    if only_findings:
        subtitle += " · nur Auffälligkeiten"
    findings_text = (
        f"{vm['bad']} VM(s) ungeschützt/ohne Backup, {vm['warn']} überfällig" if findings else "Alle Objekte geschützt und aktuell"
    )
    return ReportContent(
        title="Schutzstatus", subtitle=subtitle, kpis=kpis, findings=findings, findings_text=findings_text,
        sections=sections,
        csv_columns=["Bereich", "Name", "Protection Group", "Policies", "Letztes Backup (primär)", "Alter", "Letztes Backup (sekundär)",
                     *(["Schutzklasse"] if with_classes else []), "Status"],
        csv_rows=csv_rows,
    )
