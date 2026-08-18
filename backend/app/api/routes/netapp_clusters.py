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
from app.schemas.netapp_cluster import NetAppClusterCreate, NetAppClusterRead
from app.services.netapp_service import NetAppConnectionError, NetAppOntapService

router = APIRouter(prefix="/api/netapp/clusters", tags=["netapp-clusters"])


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


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> None:
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    db.delete(cluster)
    db.commit()
