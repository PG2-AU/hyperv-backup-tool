"""Report "Inventar" (Backlog #84 Stufe 2): alle VMs mit Cluster, Host,
Zustand, Standort, Speicherort, vCPU/RAM, Disk-Groesse, Protection Group und
Checkpoints; optional CSVs und SMB3-Freigaben mit Groesse, Storage-Zuordnung
und Standort. Gleiche Daten wie Inventory (list_vms/list_csvs/
list_smb_shares), Stand letzte Discovery. Ein Inventar hat keine
Auffaelligkeiten -- "nur bei Auffaelligkeiten senden" verschickt es nie;
gezaehlt wird nur eine Standort-Abweichung (VM-Host vs. Storage).

Auswahl (params): cluster_ids, site_ids, include_csv, include_smb (je
Standard ja). Die CSV-Datei enthaelt die VM-Liste."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.reports.base import Kpi, ReportContent, Section, fmt_bytes, fmt_dt

_STATE = {"Running": "Läuft", "Off": "Aus", "Paused": "Pausiert", "Saved": "Gespeichert"}


def build(db: Session, params: dict, now: datetime) -> ReportContent:
    from app.api.routes.vms import list_csvs, list_smb_shares, list_vms

    cluster_ids = set(params.get("cluster_ids") or [])
    site_ids = set(params.get("site_ids") or [])
    include_csv = params.get("include_csv", True)
    include_smb = params.get("include_smb", True)

    def site_ok(site) -> bool:
        return not site_ids or (site is not None and site.id in site_ids)

    vms = sorted(
        (v for v in list_vms(db, None) if (not cluster_ids or v.cluster_id in cluster_ids) and site_ok(v.host_site)),
        key=lambda v: v.name.lower(),
    )
    vm_rows, vm_levels = [], []
    mismatches = 0
    for v in vms:
        storage = [p.rstrip("\\").split("\\")[-1] for p in v.csv_paths] + list(v.smb_share_paths)
        site = v.host_site.name if v.host_site else "–"
        if v.site_mismatch:
            mismatches += 1
            site += " (Storage: " + ", ".join(v.site_mismatch_storage) + ")"
        vm_rows.append([
            v.name, v.cluster or "–", v.host or "–", _STATE.get(v.state, v.state), site, ", ".join(dict.fromkeys(storage)) or "–",
            str(v.cpu_count) if v.cpu_count else "–", fmt_bytes(v.memory_startup_bytes), fmt_bytes(v.vhdx_size_bytes),
            fmt_bytes(v.vhdx_used_bytes), ", ".join(v.resource_group_names) or "–", str(len(v.checkpoints)),
        ])
        vm_levels.append("warn" if v.site_mismatch else None)
    sections = [Section(
        "Virtuelle Maschinen",
        ["VM", "Cluster", "Host", "Zustand", "Standort", "Speicherort", "vCPU", "RAM", "Disks", "davon belegt", "Protection Group",
         "Checkpoints"],
        vm_rows, vm_levels, widths=[1.7, 1.1, 1.1, 0.7, 1.0, 1.3, 0.5, 0.7, 0.8, 0.8, 1.3, 0.85], status_col=4,
        empty_text="Keine VMs in der Auswahl.",
        note="Gelb: VM läuft an einem anderen Standort als ihr Storage." if mismatches else None,
    )]

    csvs = []
    if include_csv:
        csvs = sorted(
            (c for c in list_csvs(db, None) if (not cluster_ids or c.cluster_id in cluster_ids) and site_ok(c.site)),
            key=lambda c: c.name.lower(),
        )
        rows = [[
            c.name, c.hyperv_cluster_name or "–", c.owner_node or "–", c.state or "–", fmt_bytes(c.capacity_bytes), fmt_bytes(c.used_bytes),
            (c.lun_name or "–").split("/")[-1], f"{c.svm_name}:{c.volume_name}" if c.volume_name else "–",
            c.site.name if c.site else "–", ", ".join(c.resource_group_names) or "–",
        ] for c in csvs]
        sections.append(Section(
            "Cluster Shared Volumes",
            ["CSV", "Cluster", "Besitzer", "Zustand", "Größe", "Belegt", "LUN", "Volume", "Standort", "Protection Group"], rows,
            widths=[1.2, 1.1, 1.2, 0.8, 0.8, 0.8, 1.3, 1.6, 0.8, 1.3], empty_text="Keine CSVs in der Auswahl.",
        ))

    shares = []
    if include_smb:
        shares = sorted(
            (s for s in list_smb_shares(db, None) if not cluster_ids or s.cluster_id in cluster_ids),
            key=lambda s: f"{s.server}\\{s.share}".lower(),
        )
        rows = [[
            f"\\\\{s.server}\\{s.share}", s.hyperv_cluster_name or "–", fmt_bytes(s.capacity_bytes), fmt_bytes(s.used_bytes),
            f"{s.svm_name}:{s.volume_name}" if s.volume_name else "–", s.netapp_cluster_name or "–", ", ".join(s.resource_group_names) or "–",
        ] for s in shares]
        sections.append(Section(
            "SMB3-Freigaben", ["Freigabe", "Cluster", "Größe", "Belegt", "Volume", "NetApp-System", "Protection Group"], rows,
            widths=[2.0, 1.2, 0.8, 0.8, 1.8, 1.2, 1.4], empty_text="Keine SMB3-Freigaben in der Auswahl.",
        ))

    running = sum(1 for v in vms if v.state == "Running")
    kpis = [
        Kpi("VMs", str(len(vms))),
        Kpi("davon laufend", str(running)),
        Kpi("vCPU gesamt", str(sum(v.cpu_count or 0 for v in vms))),
        Kpi("RAM gesamt", fmt_bytes(sum(v.memory_startup_bytes or 0 for v in vms))),
        Kpi("Disks gesamt (belegt)", fmt_bytes(sum(v.vhdx_used_bytes or 0 for v in vms))),
    ]
    if include_csv:
        kpis.append(Kpi("CSVs", str(len(csvs))))
    if include_smb:
        kpis.append(Kpi("SMB3-Freigaben", str(len(shares))))
    counts = [f"{len(vms)} VMs"] + ([f"{len(csvs)} CSVs"] if include_csv else []) + ([f"{len(shares)} SMB3-Freigaben"] if include_smb else [])
    return ReportContent(
        title="Inventar", subtitle=f"Stand {fmt_dt(now)} (letzte Discovery)", kpis=kpis, findings=mismatches,
        findings_text=", ".join(counts) + (f", {mismatches} Standort-Abweichung(en)" if mismatches else ""),
        # CSV-Export: die VM-Liste (die Tabelle, die man in Excel weiterverarbeitet)
        sections=sections, csv_columns=sections[0].columns, csv_rows=vm_rows,
    )
