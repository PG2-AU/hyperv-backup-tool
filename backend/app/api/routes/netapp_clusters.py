"""NetApp-ONTAP-Cluster-Verwaltung: Hinzufuegen/Entfernen registrierter
Cluster, Verbindungstest inkl. Versions-/Health-Abfrage, sowie Umstellung
auf zertifikatsbasierte Authentifizierung.

Ein Cluster wird unabhaengig von HA-/MetroCluster-Zugehoerigkeit hinzugefuegt
(nur Mgmt-IP + Zugangsdaten); ob er Teil einer MetroCluster-Konfiguration
ist, wird nach dem Verbindungsaufbau automatisch ueber die Cluster-API
erkannt (siehe NetAppOntapService.get_cluster_summary).

TODO(iteration): Die Zertifikats-Umschaltung (NetAppOntapService.install_client_certificate)
nutzt die security/certificates- und security/accounts-REST-Ressourcen gemaess
NetApp-Dokumentation, wurde aber mangels Zugriff auf eine echte ONTAP-9.18.1-
Instanz nicht gegen echte Hardware verifiziert. Vor Produktiveinsatz gegen
eine Testinstanz pruefen.
"""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster, NetAppClusterHealth
from app.models.netapp_discovery import (
    NetAppAggregate,
    NetAppClusterPeer,
    NetAppLun,
    NetAppNetworkInterface,
    NetAppPlatform,
    NetAppSnapMirrorRelationship,
    NetAppSvm,
    NetAppSvmPeer,
    NetAppVolume,
)
from app.schemas.netapp_cluster import DiscoveryStepRead, NetAppClusterCreate, NetAppClusterRead
from app.services.netapp_service import DiscoveryData, NetAppConnectionError, NetAppOntapService

router = APIRouter(prefix="/api/netapp/clusters", tags=["netapp-clusters"])


def _persist_discovery(db: Session, cluster: NetAppCluster, data: DiscoveryData, step_success: dict[str, bool]) -> None:
    """Ersetzt je Objekttyp alle zuvor gespeicherten Discovery-Ergebnisse dieses
    Clusters durch die aktuellen -- aber nur fuer Typen, deren Discovery-Schritt
    in diesem Lauf erfolgreich war (sonst bliebe ein fehlgeschlagener Schritt
    die bereits bekannten Objekte faelschlich loeschen)."""
    now = datetime.now(timezone.utc)

    if step_success.get("svms"):
        db.query(NetAppSvm).filter(NetAppSvm.cluster_id == cluster.id).delete()
        for svm in data.svms:
            db.add(NetAppSvm(cluster_id=cluster.id, uuid=svm.uuid, name=svm.name, state=svm.state, subtype=svm.subtype, last_seen_at=now))

    if step_success.get("volumes"):
        db.query(NetAppVolume).filter(NetAppVolume.cluster_id == cluster.id).delete()
        for vol in data.volumes:
            db.add(
                NetAppVolume(
                    cluster_id=cluster.id, uuid=vol.uuid, name=vol.name, svm_name=vol.svm_name,
                    state=vol.state, size_bytes=vol.size_bytes, used_bytes=vol.used_bytes, last_seen_at=now,
                )
            )

    if step_success.get("luns"):
        db.query(NetAppLun).filter(NetAppLun.cluster_id == cluster.id).delete()
        for lun in data.luns:
            db.add(
                NetAppLun(
                    cluster_id=cluster.id, uuid=lun.uuid, name=lun.name, svm_name=lun.svm_name,
                    volume_name=lun.volume_name, state=lun.state, size_bytes=lun.size_bytes,
                    os_type=lun.os_type, last_seen_at=now,
                )
            )

    if step_success.get("cluster_peers"):
        db.query(NetAppClusterPeer).filter(NetAppClusterPeer.cluster_id == cluster.id).delete()
        for peer in data.cluster_peers:
            db.add(
                NetAppClusterPeer(
                    cluster_id=cluster.id, uuid=peer.uuid, name=peer.name,
                    remote_name=peer.remote_name, state=peer.state, last_seen_at=now,
                )
            )

    if step_success.get("svm_peers"):
        db.query(NetAppSvmPeer).filter(NetAppSvmPeer.cluster_id == cluster.id).delete()
        for peer in data.svm_peers:
            db.add(
                NetAppSvmPeer(
                    cluster_id=cluster.id, uuid=peer.uuid, svm_name=peer.svm_name,
                    peer_svm_name=peer.peer_svm_name, peer_cluster_name=peer.peer_cluster_name,
                    state=peer.state, last_seen_at=now,
                )
            )

    if step_success.get("snapmirror"):
        db.query(NetAppSnapMirrorRelationship).filter(NetAppSnapMirrorRelationship.cluster_id == cluster.id).delete()
        for rel in data.snapmirror_relationships:
            db.add(
                NetAppSnapMirrorRelationship(
                    cluster_id=cluster.id, uuid=rel.uuid, source_path=rel.source_path,
                    destination_path=rel.destination_path, state=rel.state, healthy=rel.healthy, last_seen_at=now,
                )
            )

    if step_success.get("network_interfaces"):
        db.query(NetAppNetworkInterface).filter(NetAppNetworkInterface.cluster_id == cluster.id).delete()
        for iface in data.network_interfaces:
            db.add(
                NetAppNetworkInterface(
                    cluster_id=cluster.id, uuid=iface.uuid, name=iface.name, address=iface.address,
                    svm_name=iface.svm_name, state=iface.state, last_seen_at=now,
                )
            )

    if step_success.get("platforms"):
        db.query(NetAppPlatform).filter(NetAppPlatform.cluster_id == cluster.id).delete()
        for plat in data.platforms:
            db.add(
                NetAppPlatform(
                    cluster_id=cluster.id, uuid=plat.uuid, node_name=plat.node_name, model=plat.model,
                    serial_number=plat.serial_number, ontap_version=plat.ontap_version,
                    uptime_seconds=plat.uptime_seconds, state=plat.state, last_seen_at=now,
                )
            )

    if step_success.get("aggregates"):
        db.query(NetAppAggregate).filter(NetAppAggregate.cluster_id == cluster.id).delete()
        for agg in data.aggregates:
            db.add(
                NetAppAggregate(
                    cluster_id=cluster.id, uuid=agg.uuid, name=agg.name, node_name=agg.node_name,
                    state=agg.state, size_bytes=agg.size_bytes, used_bytes=agg.used_bytes, last_seen_at=now,
                )
            )

    db.commit()


def _service_for(cluster: NetAppCluster) -> NetAppOntapService:
    if cluster.auth_method == NetAppAuthMethod.CERTIFICATE and cluster.client_cert_path and cluster.client_key_path:
        return NetAppOntapService(
            host=cluster.management_lif,
            verify_ssl=cluster.verify_ssl,
            cert_path=cluster.client_cert_path,
            key_path=cluster.client_key_path,
        )
    return NetAppOntapService(
        host=cluster.management_lif,
        verify_ssl=cluster.verify_ssl,
        username=cluster.username,
        password=decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else None,
    )


def _refresh_status(db: Session, cluster: NetAppCluster) -> NetAppCluster:
    service = _service_for(cluster)
    try:
        summary = service.get_cluster_summary()
        cluster.ontap_version = summary.ontap_version
        cluster.cluster_uuid = summary.uuid
        cluster.node_count = summary.node_count
        cluster.healthy_node_count = summary.healthy_node_count
        cluster.health = NetAppClusterHealth.HEALTHY if summary.healthy else NetAppClusterHealth.DEGRADED
        cluster.is_metrocluster = summary.is_metrocluster
        cluster.last_check_error = None
    except NetAppConnectionError as exc:
        cluster.health = NetAppClusterHealth.UNREACHABLE
        cluster.last_check_error = str(exc)
    cluster.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.get("", response_model=list[NetAppClusterRead])
def list_clusters(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_VIEW))) -> list[NetAppCluster]:
    return db.query(NetAppCluster).order_by(NetAppCluster.name).all()


@router.post("", response_model=NetAppClusterRead, status_code=status.HTTP_201_CREATED)
def create_cluster(
    payload: NetAppClusterCreate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> NetAppCluster:
    if db.query(NetAppCluster).filter(NetAppCluster.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Cluster mit diesem Namen existiert bereits")

    probe = NetAppOntapService(
        host=payload.management_lif, verify_ssl=payload.verify_ssl, username=payload.username, password=payload.password,
    )
    try:
        summary = probe.get_cluster_summary()
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Verbindung fehlgeschlagen: {exc}") from exc

    cluster = NetAppCluster(
        name=payload.name,
        management_lif=payload.management_lif,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        verify_ssl=payload.verify_ssl,
        ontap_version=summary.ontap_version,
        cluster_uuid=summary.uuid,
        node_count=summary.node_count,
        healthy_node_count=summary.healthy_node_count,
        health=NetAppClusterHealth.HEALTHY if summary.healthy else NetAppClusterHealth.DEGRADED,
        is_metrocluster=summary.is_metrocluster,
        last_checked_at=datetime.now(timezone.utc),
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.post("/{cluster_id}/verify", response_model=NetAppClusterRead)
def verify_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> NetAppCluster:
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    return _refresh_status(db, cluster)


@router.post("/{cluster_id}/enroll-certificate", response_model=NetAppClusterRead)
def enroll_certificate(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> NetAppCluster:
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    if not cluster.encrypted_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kein gespeichertes Kennwort fuer die Zertifikats-Umschaltung vorhanden",
        )

    service = NetAppOntapService(
        host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
        username=cluster.username, password=decrypt_secret(cluster.encrypted_password),
    )
    settings = get_settings()
    try:
        cert_path, key_path = service.install_client_certificate(
            cluster.username, Path(settings.netapp_cert_dir), cluster.id,
        )
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Zertifikats-Umschaltung fehlgeschlagen: {exc}") from exc

    cluster.client_cert_path = cert_path
    cluster.client_key_path = key_path
    cluster.auth_method = NetAppAuthMethod.CERTIFICATE
    db.commit()
    db.refresh(cluster)

    cluster = _refresh_status(db, cluster)
    if cluster.health == NetAppClusterHealth.UNREACHABLE:
        # Zertifikats-Login schlug fehl -> auf Kennwort-Authentifizierung
        # zurueckfallen; das Zertifikat bleibt fuer einen erneuten Versuch gespeichert.
        cluster.auth_method = NetAppAuthMethod.PASSWORD
        error = cluster.last_check_error
        db.commit()
        db.refresh(cluster)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zertifikat wurde installiert, Anmeldung damit schlug aber fehl: {error}. "
            "Zurueckgestuft auf Kennwort-Authentifizierung.",
        )
    return cluster


@router.post("/{cluster_id}/discover", response_model=list[DiscoveryStepRead])
def discover_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
):
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    service = _service_for(cluster)
    steps, data = service.run_discovery()
    step_success = {s.step: s.success for s in steps}
    _persist_discovery(db, cluster, data, step_success)
    return steps


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> None:
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    db.delete(cluster)
    db.commit()
