"""Hyper-V-Failover-Cluster-Verwaltung: Hinzufuegen/Entfernen registrierter
Cluster sowie Verbindungstest per WinRM (siehe HyperVService.get_cluster_summary).

Registriert wird der Cluster (Cluster Name Object / Management-IP), nicht die
einzelnen Knoten -- vgl. NetApp-Cluster-Verwaltung in netapp_clusters.py.
"""

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.hyperv_cluster import HyperVCluster, HyperVClusterHealth
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppLun
from app.schemas.hyperv_cluster import HyperVClusterCreate, HyperVClusterRead, HyperVReachabilityCheck
from app.schemas.netapp_cluster import DiscoveryStepRead
from app.services.hyperv_service import HyperVConnectionError, HyperVService, check_reachability

router = APIRouter(prefix="/api/hyperv/clusters", tags=["hyperv-clusters"])

_CSV_NAME_RE = re.compile(r"ClusterStorage\\([^\\]+)\\", re.IGNORECASE)


def _parse_csv_name(vhd_path: str) -> str | None:
    match = _CSV_NAME_RE.search(vhd_path)
    return match.group(1) if match else None


def _get_cluster_or_404(db: Session, cluster_id: str) -> HyperVCluster:
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    return cluster


def _service_for(cluster: HyperVCluster) -> HyperVService:
    return HyperVService(get_settings(), cluster.management_address, use_https=cluster.use_https)


def _apply_summary(cluster: HyperVCluster, summary) -> None:
    cluster.hyperv_cluster_name = summary.cluster_name
    cluster.node_count = summary.node_count
    cluster.healthy_node_count = summary.healthy_node_count
    cluster.health = (
        HyperVClusterHealth.HEALTHY
        if summary.node_count > 0 and summary.healthy_node_count == summary.node_count
        else HyperVClusterHealth.DEGRADED
    )
    cluster.last_check_error = None


def _refresh_node_reachability(cluster: HyperVCluster, service: HyperVService, username: str, password: str) -> None:
    """Ergaenzt cluster.unreachable_nodes_json um das Ergebnis eines
    direkten Erreichbarkeits-Checks pro Cluster-Knoten (siehe
    HyperVService.check_node_reachability) -- rein additiv zur eigentlichen
    Cluster-Health oben: ein Fehler hier (z.B. Get-ClusterNode selbst
    schlaegt fehl) laesst den Health-Check/die Cluster-Anlage NICHT
    scheitern, sondern belaesst nur den zuletzt bekannten Node-Status."""
    try:
        cno_session = service.connect(username, password, read_timeout_sec=15, operation_timeout_sec=10)
        node_results = service.check_node_reachability(cno_session, username, password)
        unreachable = [
            {"name": r.name, "address": r.address, "error": r.error} for r in node_results if not r.reachable
        ]
        cluster.unreachable_nodes_json = json.dumps(unreachable) if unreachable else None
    except Exception:
        pass


def _refresh_status(db: Session, cluster: HyperVCluster) -> HyperVCluster:
    service = _service_for(cluster)
    try:
        password = decrypt_secret(cluster.encrypted_password)
        summary = service.get_cluster_summary(cluster.username, password)
        _apply_summary(cluster, summary)
        _refresh_node_reachability(cluster, service, cluster.username, password)
    except HyperVConnectionError as exc:
        cluster.health = HyperVClusterHealth.UNREACHABLE
        cluster.last_check_error = str(exc)
    cluster.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.get("", response_model=list[HyperVClusterRead])
def list_clusters(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[HyperVCluster]:
    return db.query(HyperVCluster).order_by(HyperVCluster.name).all()


@router.post("/check-reachability")
def check_reachability_route(
    payload: HyperVReachabilityCheck, user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> dict:
    port = 5986 if payload.use_https else 5985
    try:
        check_reachability(payload.management_address, port)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "reachable"}


@router.post("", response_model=HyperVClusterRead, status_code=status.HTTP_201_CREATED)
def create_cluster(
    payload: HyperVClusterCreate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> HyperVCluster:
    if db.query(HyperVCluster).filter(HyperVCluster.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Cluster mit diesem Namen existiert bereits")

    probe = HyperVService(get_settings(), payload.management_address, use_https=payload.use_https)
    try:
        summary = probe.get_cluster_summary(payload.username, payload.password)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Verbindung fehlgeschlagen: {exc}") from exc

    cluster = HyperVCluster(
        name=payload.name,
        management_address=payload.management_address,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        use_https=payload.use_https,
        last_checked_at=datetime.now(timezone.utc),
    )
    _apply_summary(cluster, summary)
    _refresh_node_reachability(cluster, probe, payload.username, payload.password)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.post("/{cluster_id}/verify", response_model=HyperVClusterRead)
def verify_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> HyperVCluster:
    cluster = _get_cluster_or_404(db, cluster_id)
    return _refresh_status(db, cluster)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)
    # HyperVVm/-Vhd/-Csv sind zwar mit ForeignKey(..., ondelete="CASCADE")
    # deklariert, aber SQLite erzwingt das nur, wenn PRAGMA foreign_keys=ON
    # pro Verbindung gesetzt wird -- das passiert in dieser App nirgends,
    # die CASCADE-Angabe im Modell ist also reine Dokumentation ohne
    # Wirkung. Ohne diesen expliziten Cleanup blieben discoverte VMs/CSVs
    # des geloeschten Clusters als Stale-Entries stehen (live beobachtet:
    # fuehrte bei erneutem Hinzufuegen desselben Clusters zu doppelten
    # VM-Eintraegen im Inventory, siehe Backlog).
    db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id).delete()
    db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id).delete()
    db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).delete()
    db.delete(cluster)
    db.commit()


def _run_discovery(db: Session, cluster: HyperVCluster) -> list:
    """Kernlogik von discover_cluster() -- ausgelagert, damit der periodische
    Discovery-Job (app.core.scheduler) und der manuelle 'Discover'-Button in
    der GUI exakt denselben Code nutzen, statt ihn zu duplizieren."""
    service = _service_for(cluster)
    steps, data = service.run_discovery(cluster.username, decrypt_secret(cluster.encrypted_password))

    # Nur ersetzen, wenn mindestens ein Knoten erfolgreich abgefragt wurde --
    # sonst wuerde ein voruebergehend nicht erreichbarer Cluster die bereits
    # bekannten VMs faelschlich loeschen (analog zur NetApp-Discovery).
    if any(s.success for s in steps if s.step == "vms"):
        now = datetime.now(timezone.utc)
        db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster.id).delete()
        db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster.id).delete()
        for vm in data.vms:
            db.add(
                HyperVVm(
                    cluster_id=cluster.id, vm_uuid=vm.id, name=vm.name, state=vm.state, host_name=vm.host, last_seen_at=now,
                    cpu_count=vm.cpu_count, generation=vm.generation,
                    memory_startup_bytes=vm.memory_startup_bytes, memory_minimum_bytes=vm.memory_minimum_bytes,
                    memory_maximum_bytes=vm.memory_maximum_bytes, dynamic_memory_enabled=vm.dynamic_memory_enabled,
                    network_adapters=[
                        {"name": n.name, "mac_address": n.mac_address, "switch_name": n.switch_name, "vlan_id": n.vlan_id}
                        for n in vm.network_adapters
                    ],
                    pci_devices=vm.pci_devices,
                )
            )
            for vhd in vm.vhds:
                db.add(
                    HyperVVhd(
                        cluster_id=cluster.id, vm_uuid=vm.id, vm_name=vm.name, path=vhd.path,
                        csv_name=_parse_csv_name(vhd.path), size_bytes=vhd.size_bytes, used_bytes=vhd.used_bytes,
                        last_seen_at=now,
                    )
                )
        db.commit()

    if any(s.success for s in steps if s.step == "csvs"):
        now = datetime.now(timezone.utc)
        # Seriennummer -> NetApp-LUN ueber alle registrierten NetApp-Cluster
        # hinweg (die Windows-Disk-Seriennummer entspricht ONTAP's
        # lun.serial_number, siehe list_csvs()); Clustername separat
        # aufloesen, da NetAppLun selbst nur die cluster_id speichert.
        netapp_cluster_names = {c.id: c.ontap_cluster_name or c.name for c in db.query(NetAppCluster).all()}
        luns_by_serial = {
            lun.serial_number: lun for lun in db.query(NetAppLun).all() if lun.serial_number
        }
        db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster.id).delete()
        for csv in data.csvs:
            lun = luns_by_serial.get(csv.disk_serial_number) if csv.disk_serial_number else None
            db.add(
                HyperVCsv(
                    cluster_id=cluster.id, name=csv.name, path=csv.volume_path, owner_node=csv.owner_node,
                    state=csv.state, capacity_bytes=csv.capacity_bytes, used_bytes=csv.used_bytes,
                    disk_serial_number=csv.disk_serial_number,
                    netapp_lun_id=lun.id if lun else None,
                    netapp_lun_name=lun.name if lun else None,
                    netapp_volume_name=lun.volume_name if lun else None,
                    netapp_svm_name=lun.svm_name if lun else None,
                    netapp_cluster_name=netapp_cluster_names.get(lun.cluster_id) if lun else None,
                    last_seen_at=now,
                )
            )
        db.commit()

    return steps


@router.post("/{cluster_id}/discover", response_model=list[DiscoveryStepRead])
def discover_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
):
    cluster = _get_cluster_or_404(db, cluster_id)
    return _run_discovery(db, cluster)
