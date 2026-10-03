"""Report "Wiederherstellungspunkte" (Backlog #84): je VM, wie weit man
zurueck kann -- Anzahl, aeltester und neuester Punkt primaer und sekundaer
(SnapMirror-Ziel). Grundlage wie im Dialog "Vorhandene Backups": primaer =
BackupRunSnapshot mit success, sekundaer = mindestens eine bestaetigte Kopie
auf einem Ziel; Laeufe, in denen die VM als "nicht erfasst" markiert ist
(zwischenzeitlich verschoben), zaehlen fuer sie nicht.

Auswahl (params): cluster_ids, resource_group_ids, vm_names, include_secondary
(Standard ja), only_findings (nur VMs ohne Wiederherstellungspunkt)."""

from datetime import datetime

from sqlalchemy.orm import Session, selectinload

from app.core.reports.base import Kpi, ReportContent, Section, fmt_dt, fmt_num
from app.models.backup_run import BackupRunSnapshot, BackupRunVmConfig
from app.models.resource_group import ResourceGroup


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    from app.api.routes.vms import list_vms

    cluster_ids = set(params.get("cluster_ids") or [])
    group_ids = set(params.get("resource_group_ids") or [])
    vm_names = set(params.get("vm_names") or [])
    include_secondary = params.get("include_secondary", True)
    only_findings = bool(params.get("only_findings"))
    group_names = {g.name for g in db.query(ResourceGroup).filter(ResourceGroup.id.in_(group_ids))} if group_ids else set()

    vms = [
        v for v in list_vms(db, None)
        if (not cluster_ids or v.cluster_id in cluster_ids)
        and (not group_names or group_names & set(v.resource_group_names))
        and (not vm_names or v.name in vm_names)
    ]
    wanted = {v.name for v in vms}
    not_captured = {(c.run_id, c.vm_name) for c in db.query(BackupRunVmConfig).filter(BackupRunVmConfig.not_captured.is_(True))}
    primary: dict[str, list[datetime]] = {name: [] for name in wanted}
    secondary: dict[str, list[datetime]] = {name: [] for name in wanted}
    for row in db.query(BackupRunSnapshot).options(selectinload(BackupRunSnapshot.destinations)).all():
        present = any(d.present for d in row.destinations)
        for name in row.vm_names or []:
            if name not in wanted or (row.run_id, name) in not_captured:
                continue
            if row.success:
                primary[name].append(row.created_at)
            if present:
                secondary[name].append(row.created_at)

    rows, levels, csv_rows = [], [], []
    without = 0
    oldest_any: datetime | None = None
    for vm in sorted(vms, key=lambda v: v.name.lower()):
        p, s = sorted(primary[vm.name]), sorted(secondary[vm.name]) if include_secondary else []
        total = len(p) + len(s)
        if total == 0:
            without += 1
        for candidate in (p[:1] + s[:1]):
            oldest_any = candidate if oldest_any is None or candidate < oldest_any else oldest_any
        level = "bad" if total == 0 else ("warn" if not p else "ok")
        if only_findings and level == "ok":
            continue
        row = [vm.name, vm.cluster or "–", ", ".join(vm.resource_group_names) or "–", str(len(p)), fmt_dt(p[0] if p else None), fmt_dt(p[-1] if p else None)]
        if include_secondary:
            row += [str(len(s)), fmt_dt(s[0] if s else None), fmt_dt(s[-1] if s else None)]
        rows.append(row)
        levels.append(level)
        csv_rows.append(row)

    columns = ["VM", "Cluster", "Protection Group", "Primär: Anzahl", "ältester", "neuester"]
    widths = [2.0, 1.2, 1.6, 0.8, 1.2, 1.2]
    if include_secondary:
        columns += ["Sekundär: Anzahl", "ältester", "neuester"]
        widths += [0.9, 1.2, 1.2]
    counts = [len(primary[v.name]) + (len(secondary[v.name]) if include_secondary else 0) for v in vms]
    kpis = [
        Kpi("VMs", str(len(vms))),
        Kpi("ohne Wiederherstellungspunkt", str(without), "bad" if without else "ok"),
        Kpi("Ø Punkte je VM", fmt_num(sum(counts) / len(counts)) if counts else "–"),
        Kpi("ältester Punkt", fmt_dt(oldest_any)),
    ]
    scope = []
    if cluster_ids:
        scope.append(f"{len(cluster_ids)} Cluster")
    if group_names:
        scope.append("Protection Group " + ", ".join(sorted(group_names)))
    if vm_names:
        scope.append(f"{len(vm_names)} ausgewählte VM(s)")
    subtitle = f"Stand {fmt_dt(now)}" + (f" · {' · '.join(scope)}" if scope else "") + ("" if include_secondary else " · nur primär")
    return ReportContent(
        title="Wiederherstellungspunkte", subtitle=subtitle, kpis=kpis, findings=without,
        findings_text=f"{without} VM(s) ohne Wiederherstellungspunkt" if without else "Alle VMs haben Wiederherstellungspunkte",
        sections=[Section(
            "Wiederherstellungspunkte je VM", columns, rows, levels, widths,
            note="Markierung am VM-Namen -- Gelb: nur noch auf dem SnapMirror-Ziel vorhanden. Rot: kein Wiederherstellungspunkt.",
            empty_text="Keine Auffälligkeiten." if only_findings else "Keine VMs in der Auswahl.", status_col=0,
        )],
        csv_columns=columns, csv_rows=csv_rows,
    )
