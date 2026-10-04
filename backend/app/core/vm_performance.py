"""VM-Performance sammeln (Backlog #80 Stufe 2): je Hyper-V-Cluster ein
lesender WinRM-Aufruf (HyperVService.storage_qos_flows, Storage QoS), die
Werte je VM und CSV zusammengefasst als VmPerfSample abgelegt. Laeuft als
Hintergrundjob im Abstand aus Settings > Hintergrundjobs (Standard 5 Minuten,
0 = aus).

Storage QoS liefert je Flow (VM-Disk) einen gleitenden Mittelwert der letzten
Sekunden -- ein Messpunkt ist also eine Momentaufnahme, kein Mittel ueber das
ganze Sammelintervall. Fuer Trends und "wer macht die Last" reicht das; fuer
lueckenlose Spitzen-Erkennung muesste man oefter messen.

Aufbewahrung: Rohpunkte 7 Tage, danach Stundenmittel (90 Tage)."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv
from app.models.vm_performance import VmPerfSample
from app.services.hyperv_service import HyperVService

log = logging.getLogger(__name__)

RAW_DAYS = 7
HOURLY_DAYS = 90

# Letzter Sammelstand je Cluster fuer die Anzeige (ein Prozess, siehe uvicorn-Aufruf).
STATUS: dict[str, dict] = {}


def _csv_name(path: str, folder_to_csv: dict[str, str]) -> str | None:
    """'C:\\ClusterStorage\\Volume3\\VM\\x.vhdx' -> CSV-Ressourcenname (der
    Ordnername kann vom umbenennbaren CSV-Namen abweichen, Abgleich wie in der
    Discovery ohne Gross-/Kleinschreibung)."""
    parts = path.replace("/", "\\").split("\\")
    lowered = [p.lower() for p in parts]
    if "clusterstorage" not in lowered:
        return None
    index = lowered.index("clusterstorage")
    if index + 1 >= len(parts):
        return None
    folder = parts[index + 1]
    return folder_to_csv.get(folder.lower(), folder)


def aggregate(flows: list[dict], folder_to_csv: dict[str, str]) -> list[dict]:
    """Flows (je Disk) -> je (VM, CSV): IOPS und Bandbreite summiert, Latenz
    IOPS-gewichtet (ohne IO: hoechster Disk-Wert)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for flow in flows:
        csv = _csv_name(flow.get("Path") or "", folder_to_csv)
        vm_id = (flow.get("VmId") or "").lower()
        if not csv or not vm_id:
            continue
        groups[(vm_id, csv)].append(flow)
    result = []
    for (vm_id, csv), items in groups.items():
        iops = sum(f.get("Iops") or 0.0 for f in items)
        if iops > 0:
            latency = sum((f.get("Iops") or 0.0) * (f.get("LatencyMs") or 0.0) for f in items) / iops
        else:
            latency = max((f.get("LatencyMs") or 0.0) for f in items)
        node = items[0].get("Node") or None
        result.append({
            "vm_id": vm_id, "vm_name": items[0].get("VmName") or vm_id, "csv_name": csv,
            "host": node.split(".")[0] if node else None,
            "iops": iops, "latency_ms": latency, "bandwidth": sum(f.get("Bandwidth") or 0.0 for f in items), "disk_count": len(items),
        })
    return result


def collect_cluster(db: Session, cluster: HyperVCluster, now: datetime) -> int:
    service = HyperVService(get_settings(), cluster.management_address, use_https=cluster.use_https, node_hostname=cluster.hyperv_cluster_name)
    session = service.connect(cluster.username, decrypt_secret(cluster.encrypted_password), read_timeout_sec=60, operation_timeout_sec=50)
    flows = service.storage_qos_flows(session)
    folder_to_csv = {}
    for csv in db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster.id):
        folder = (csv.path or "").rstrip("\\").split("\\")[-1]
        if folder:
            folder_to_csv[folder.lower()] = csv.name
    rows = aggregate(flows, folder_to_csv)
    for row in rows:
        db.add(VmPerfSample(cluster_id=cluster.id, resolution="raw", sampled_at=now, **row))
    db.commit()
    return len(rows)


def downsample_and_purge(db: Session, now: datetime) -> None:
    """Rohpunkte aelter als RAW_DAYS zu Stundenmitteln verdichten (nur ganze
    Stunden), Stundenwerte aelter als HOURLY_DAYS loeschen."""
    cutoff = (now - timedelta(days=RAW_DAYS)).replace(minute=0, second=0, microsecond=0)
    old = (
        db.query(VmPerfSample)
        .filter(VmPerfSample.resolution == "raw", VmPerfSample.sampled_at < cutoff)
        .all()
    )
    if old:
        buckets: dict[tuple, list[VmPerfSample]] = defaultdict(list)
        for s in old:
            hour = s.sampled_at.replace(minute=0, second=0, microsecond=0)
            buckets[(s.cluster_id, s.vm_id, s.csv_name, hour)].append(s)
        for (cluster_id, vm_id, csv_name, hour), items in buckets.items():
            iops = sum(i.iops for i in items) / len(items)
            weight = sum(i.iops for i in items)
            latency = (sum(i.iops * i.latency_ms for i in items) / weight) if weight else sum(i.latency_ms for i in items) / len(items)
            db.add(VmPerfSample(
                cluster_id=cluster_id, vm_id=vm_id, vm_name=items[-1].vm_name, host=items[-1].host, csv_name=csv_name, resolution="1h",
                sampled_at=hour, iops=iops, latency_ms=latency, bandwidth=sum(i.bandwidth for i in items) / len(items),
                disk_count=max(i.disk_count for i in items),
            ))
        for s in old:
            db.delete(s)
        db.commit()
    db.query(VmPerfSample).filter(VmPerfSample.sampled_at < now - timedelta(days=HOURLY_DAYS)).delete()
    db.commit()


def run_collection(db: Session, write_log) -> None:
    """Ein Sammellauf ueber alle Hyper-V-Cluster. write_log(message, level)
    nur bei Zustandswechsel (Fehler/wieder ok), sonst kein Log-Spam alle 5 min."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for cluster in db.query(HyperVCluster).all():
        previous = STATUS.get(cluster.id, {})
        try:
            count = collect_cluster(db, cluster, now)
            STATUS[cluster.id] = {"at": now, "error": None, "vm_rows": count}
            if previous.get("error"):
                write_log(f"VM-Performance: Cluster '{cluster.name}' wieder erreichbar ({count} VM/CSV-Werte)", "INFO")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            message = str(exc)[:500]
            STATUS[cluster.id] = {"at": now, "error": message, "vm_rows": 0}
            if previous.get("error") != message:
                write_log(f"VM-Performance: Cluster '{cluster.name}' konnte nicht abgefragt werden: {message}", "WARNING")
    downsample_and_purge(db, now)
