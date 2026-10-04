"""Report "Kapazität und Prognose" (Backlog #84 Stufe 2): Belegung von
Aggregaten, Volumes, LUNs, CSVs und SMB3-Freigaben, Zuwachs der letzten 30
Tage und "voll in N Tagen" -- dieselbe Prognose wie im Kapazitaetsverlauf
und im Prognose-Alarm (app.core.capacity_forecast). Bei Volumes zusaetzlich
Snapshot-Anteil und Ueberbuchung (Summe der LUN-Groessen / Volume-Groesse).

Status: kritisch ab crit_percent belegt oder voll innerhalb von 28 Tagen bei
echtem Zuwachs; Warnung ab warn_percent oder voll innerhalb von 90 Tagen.

Auswahl (params): netapp_cluster_ids, object_types (aggregate, volume, lun,
csv, smb_share; Standard aggregate, volume, csv, smb_share), hyperv_only
(nur von Hyper-V genutzte Volumes/LUNs, Standard ja), warn_percent (85),
crit_percent (95), only_findings."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.capacity_forecast import HORIZON_DAYS, WINDOW_DAYS, forecast
from app.core.capacity_history import capacity_key
from app.core.reports.base import Kpi, ReportContent, Section, fmt_bytes, fmt_num
from app.models.capacity_history import CapacitySample
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppAggregate, NetAppLun, NetAppVolume

TYPE_LABEL = {"aggregate": "Aggregate", "volume": "Volumes", "lun": "LUNs", "csv": "Cluster Shared Volumes", "smb_share": "SMB3-Freigaben"}
DEFAULT_TYPES = ["aggregate", "volume", "csv", "smb_share"]
WATCH_DAYS = 90


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    from app.api.routes.vms import list_csvs, list_smb_shares
    from app.core.scheduler import _hyperv_referenced_keys

    types = [t for t in TYPE_LABEL if t in set(params.get("object_types") or DEFAULT_TYPES)]
    cluster_ids = set(params.get("netapp_cluster_ids") or [])
    hyperv_only = params.get("hyperv_only", True)
    warn_percent = int(params.get("warn_percent") or 85)
    crit_percent = int(params.get("crit_percent") or 95)
    only_findings = bool(params.get("only_findings"))
    cluster_names = {c.id: c.name for c in db.query(NetAppCluster)}
    selected_names = {cluster_names.get(i) for i in cluster_ids}

    samples: dict[tuple[str, str], list] = defaultdict(list)
    for row in (
        db.query(CapacitySample.object_type, CapacitySample.object_key, CapacitySample.sampled_at,
                 CapacitySample.used_bytes, CapacitySample.capacity_bytes)
        .filter(CapacitySample.object_type.in_(types), CapacitySample.sampled_at >= now - timedelta(days=WINDOW_DAYS + 1))
        .all()
    ):
        samples[(row.object_type, row.object_key)].append((row.sampled_at, row.used_bytes, row.capacity_bytes))

    csvs = list_csvs(db, None) if {"csv", "volume", "lun"} & set(types) else []
    shares = list_smb_shares(db, None) if {"smb_share", "volume"} & set(types) else []
    referenced_luns, referenced_volumes = _hyperv_referenced_keys(db) if hyperv_only else (set(), set())
    smb_volumes = {(s.svm_name, s.volume_name) for s in shares if s.volume_name}

    def volume_in_scope(v) -> bool:
        if cluster_ids and v.cluster_id not in cluster_ids:
            return False
        return not hyperv_only or (v.cluster_id, v.svm_name, v.name) in referenced_volumes or (v.svm_name, v.name) in smb_volumes

    lun_sizes: dict[tuple[str, str, str], int] = defaultdict(int)
    all_luns = db.query(NetAppLun).all()
    for lun in all_luns:
        lun_sizes[(lun.cluster_id, lun.svm_name, lun.volume_name)] += lun.size_bytes or 0

    # (Typ, System, Name, Groesse, belegt, Kennzeichen fuer die Prognose, Zusatz)
    objects: list[tuple[str, str, str, int | None, int | None, str, str]] = []
    if "aggregate" in types:
        for a in db.query(NetAppAggregate).all():
            if not cluster_ids or a.cluster_id in cluster_ids:
                extra = f"Effizienz {fmt_num(a.efficiency_ratio)}:1" if a.efficiency_ratio else ""
                objects.append(("aggregate", cluster_names.get(a.cluster_id, "–"), a.name, a.size_bytes, a.used_bytes,
                                capacity_key("aggregate", a.cluster_id, uuid=a.uuid, name=a.name), extra))
    if "volume" in types:
        for v in db.query(NetAppVolume).all():
            if not volume_in_scope(v):
                continue
            extra = []
            if v.snapshot_used_bytes:
                extra.append(f"Snapshots {fmt_bytes(v.snapshot_used_bytes)}")
            provisioned = lun_sizes.get((v.cluster_id, v.svm_name, v.name), 0)
            if provisioned and v.size_bytes:
                extra.append(f"LUNs {fmt_num(provisioned / v.size_bytes * 100, 0)} % der Größe")
            objects.append(("volume", f"{cluster_names.get(v.cluster_id, '–')} / {v.svm_name or '–'}", v.name, v.size_bytes, v.used_bytes,
                            capacity_key("volume", v.cluster_id, uuid=v.uuid, name=v.name), ", ".join(extra)))
    if "lun" in types:
        for lun in all_luns:
            if cluster_ids and lun.cluster_id not in cluster_ids:
                continue
            if hyperv_only and lun.id not in referenced_luns:
                continue
            objects.append(("lun", f"{cluster_names.get(lun.cluster_id, '–')} / {lun.svm_name or '–'}", lun.name.split("/")[-1],
                            lun.size_bytes, lun.used_bytes, capacity_key("lun", lun.cluster_id, uuid=lun.uuid, name=lun.name),
                            f"Volume {lun.volume_name}" if lun.volume_name else ""))
    if "csv" in types:
        for c in csvs:
            if cluster_ids and c.netapp_cluster_name not in selected_names:
                continue
            objects.append(("csv", c.hyperv_cluster_name or "–", c.name, c.capacity_bytes, c.used_bytes,
                            capacity_key("csv", c.cluster_id or "", name=c.name),
                            f"LUN {(c.lun_name or '').split('/')[-1]}" if c.lun_name else ""))
    if "smb_share" in types:
        for s in shares:
            if cluster_ids and s.netapp_cluster_name not in selected_names:
                continue
            unc = f"\\\\{s.server}\\{s.share}"
            objects.append(("smb_share", s.hyperv_cluster_name or "–", unc, s.capacity_bytes, s.used_bytes,
                            capacity_key("smb_share", s.cluster_id or "", name=unc),
                            f"Volume {s.volume_name}" if s.volume_name else ""))

    rows: dict[str, list] = defaultdict(list)
    critical = warning = soon = 0
    for kind, system, name, size, used, key, extra in objects:
        prediction = forecast(samples.get((kind, key), []))
        percent = used / size * 100 if size and used is not None else None
        growing = prediction is not None and prediction.growth_bytes_per_day > 0
        days = prediction.days_to_full if growing else None
        level = "ok"
        if (percent is not None and percent >= crit_percent) or (days is not None and days <= HORIZON_DAYS):
            level = "bad"
        elif (percent is not None and percent >= warn_percent) or (days is not None and days <= WATCH_DAYS):
            level = "warn"
        critical += level == "bad"
        warning += level == "warn"
        soon += days is not None and days <= HORIZON_DAYS
        if only_findings and level == "ok":
            continue
        growth = fmt_bytes(prediction.growth_bytes_per_day * 30) if prediction is not None else "–"
        if days is None:
            full = "–" if prediction is not None else "zu wenig Messwerte"
        else:
            if days < 1:
                full = "bereits voll"
            elif days > 3650:  # kaum Zuwachs -- kein sinnvolles Datum (und datetime liefe ueber)
                full = "> 10 Jahre"
            else:
                full = f"ca. {int(days)} Tage ({(prediction.full_at):%d.%m.%Y})"
        status = {"bad": "Kritisch", "warn": "Beobachten", "ok": "OK"}[level]
        rows[kind].append(((0 if days is None else 1, -(days or 0), percent or 0), [
            system, name, fmt_bytes(size), fmt_bytes(used), f"{fmt_num(percent, 0)} %" if percent is not None else "–",
            growth, full, extra or "–", status,
        ], level))

    columns = ["System", "Name", "Größe", "Belegt", "Belegt %", "Zuwachs / 30 Tage", "Voll in", "Hinweis", "Status"]
    sections, csv_rows = [], []
    for kind in types:
        items = sorted(rows.get(kind, []), key=lambda r: r[0], reverse=True)
        sections.append(Section(
            TYPE_LABEL[kind], columns, [r[1] for r in items], [r[2] for r in items],
            widths=[1.6, 2.0, 0.9, 0.9, 0.7, 1.0, 1.4, 1.8, 0.8],
            empty_text="Keine Auffälligkeiten." if only_findings else "Keine Objekte in der Auswahl.",
        ))
        csv_rows += [[TYPE_LABEL[kind]] + r[1] for r in items]
    sections[-1].note = (
        f"Prognose: lineare Regression über die täglichen Messwerte der letzten {WINDOW_DAYS} Tage (mind. 5 Messtage über 1 Woche). "
        f"Kritisch: ab {crit_percent} % belegt oder voll in ≤ {HORIZON_DAYS} Tagen. Beobachten: ab {warn_percent} % oder voll in ≤ {WATCH_DAYS} Tagen."
    ) if sections else None

    kpis = [
        Kpi("Objekte", str(len(objects))),
        Kpi("kritisch", str(critical), "bad" if critical else "ok"),
        Kpi("beobachten", str(warning), "warn" if warning else "ok"),
        Kpi(f"voll in ≤ {HORIZON_DAYS} Tagen", str(soon), "bad" if soon else "ok"),
    ]
    scope = [", ".join(TYPE_LABEL[t] for t in types)]
    if cluster_ids:
        scope.append("System " + ", ".join(sorted(n for n in selected_names if n)))
    if hyperv_only:
        scope.append("nur von Hyper-V genutzt")
    findings = critical + warning
    return ReportContent(
        title="Kapazität und Prognose", subtitle=" · ".join(scope), kpis=kpis, findings=findings,
        findings_text=f"{critical} kritisch, {warning} zu beobachten" if findings else "Keine Kapazitätsengpässe",
        sections=sections, csv_columns=["Typ"] + columns, csv_rows=csv_rows,
    )
