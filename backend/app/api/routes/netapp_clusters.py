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
    NetAppIgroup,
    NetAppLun,
    NetAppNetworkInterface,
    NetAppPlatform,
    NetAppSchedule,
    NetAppSnapMirrorPolicy,
    NetAppSnapMirrorRelationship,
    NetAppSvm,
    NetAppSvmPeer,
    NetAppVolume,
)
from app.schemas.netapp_cluster import DiscoveryStepRead, NetAppClusterCreate, NetAppClusterRead
from app.schemas.netapp_write import (
    ClusterPeerCreate,
    IgroupCreate,
    LunCreate,
    LunMapCreate,
    LunUpdate,
    ScheduleCreate,
    SnapmirrorPolicyCreate,
    SnapmirrorPolicyUpdate,
    SnapmirrorRelationshipCreate,
    SnapmirrorRelationshipUpdate,
    SvmPeerCreate,
    VolumeCreate,
    VolumeUpdate,
)
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
            db.add(
                NetAppSvm(
                    cluster_id=cluster.id, uuid=svm.uuid, name=svm.name, state=svm.state, subtype=svm.subtype,
                    allowed_protocols=svm.allowed_protocols, data_services=svm.data_services, last_seen_at=now,
                )
            )

    if step_success.get("volumes"):
        db.query(NetAppVolume).filter(NetAppVolume.cluster_id == cluster.id).delete()
        for vol in data.volumes:
            db.add(
                NetAppVolume(
                    cluster_id=cluster.id, uuid=vol.uuid, name=vol.name, svm_name=vol.svm_name,
                    state=vol.state, size_bytes=vol.size_bytes, used_bytes=vol.used_bytes,
                    percent_used=vol.percent_used, security_style=vol.security_style, language=vol.language,
                    snapshot_autodelete_enabled=vol.snapshot_autodelete_enabled, autosize_mode=vol.autosize_mode,
                    snapshot_policy_name=vol.snapshot_policy_name, encryption_enabled=vol.encryption_enabled,
                    snapmirror_protected=vol.snapmirror_protected, last_seen_at=now,
                )
            )

    if step_success.get("luns"):
        db.query(NetAppLun).filter(NetAppLun.cluster_id == cluster.id).delete()
        for lun in data.luns:
            db.add(
                NetAppLun(
                    cluster_id=cluster.id, uuid=lun.uuid, name=lun.name, svm_name=lun.svm_name,
                    volume_name=lun.volume_name, state=lun.state, size_bytes=lun.size_bytes,
                    os_type=lun.os_type, mapped_igroups=lun.mapped_igroups,
                    serial_number=lun.serial_number, last_seen_at=now,
                )
            )

    if step_success.get("igroups"):
        db.query(NetAppIgroup).filter(NetAppIgroup.cluster_id == cluster.id).delete()
        for ig in data.igroups:
            db.add(
                NetAppIgroup(
                    cluster_id=cluster.id, uuid=ig.uuid, name=ig.name, svm_name=ig.svm_name,
                    os_type=ig.os_type, protocol=ig.protocol, initiator_count=ig.initiator_count, last_seen_at=now,
                )
            )

    if step_success.get("cluster_peers"):
        db.query(NetAppClusterPeer).filter(NetAppClusterPeer.cluster_id == cluster.id).delete()
        for peer in data.cluster_peers:
            db.add(
                NetAppClusterPeer(
                    cluster_id=cluster.id, uuid=peer.uuid, name=peer.name,
                    remote_name=peer.remote_name, state=peer.state,
                    peer_ip_addresses=peer.peer_ip_addresses, local_ip_addresses=peer.local_ip_addresses,
                    last_seen_at=now,
                )
            )

    if step_success.get("svm_peers"):
        db.query(NetAppSvmPeer).filter(NetAppSvmPeer.cluster_id == cluster.id).delete()
        for peer in data.svm_peers:
            db.add(
                NetAppSvmPeer(
                    cluster_id=cluster.id, uuid=peer.uuid, svm_name=peer.svm_name,
                    peer_svm_name=peer.peer_svm_name, peer_cluster_name=peer.peer_cluster_name,
                    state=peer.state, applications=peer.applications, last_seen_at=now,
                )
            )

    if step_success.get("snapmirror"):
        db.query(NetAppSnapMirrorRelationship).filter(NetAppSnapMirrorRelationship.cluster_id == cluster.id).delete()
        for rel in data.snapmirror_relationships:
            db.add(
                NetAppSnapMirrorRelationship(
                    cluster_id=cluster.id, uuid=rel.uuid, source_path=rel.source_path,
                    destination_path=rel.destination_path, state=rel.state, healthy=rel.healthy,
                    lag_time=rel.lag_time, last_transfer_size_bytes=rel.last_transfer_size_bytes,
                    last_transfer_error=rel.last_transfer_error, schedule_name=rel.schedule_name,
                    policy_name=rel.policy_name, destination_cluster_name=rel.destination_cluster_name, last_seen_at=now,
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
                    state=agg.state, size_bytes=agg.size_bytes, used_bytes=agg.used_bytes,
                    used_percent=agg.used_percent, efficiency_ratio=agg.efficiency_ratio,
                    efficiency_ratio_wo_snapshots=agg.efficiency_ratio_wo_snapshots, last_seen_at=now,
                )
            )

    if step_success.get("snapmirror_policies"):
        db.query(NetAppSnapMirrorPolicy).filter(NetAppSnapMirrorPolicy.cluster_id == cluster.id).delete()
        for pol in data.snapmirror_policies:
            db.add(
                NetAppSnapMirrorPolicy(
                    cluster_id=cluster.id, uuid=pol.uuid, name=pol.name, svm_name=pol.svm_name,
                    scope=pol.scope, type=pol.type, comment=pol.comment, rules_json=pol.rules_json, last_seen_at=now,
                )
            )

    if step_success.get("schedules"):
        db.query(NetAppSchedule).filter(NetAppSchedule.cluster_id == cluster.id).delete()
        for sched in data.schedules:
            db.add(
                NetAppSchedule(
                    cluster_id=cluster.id, uuid=sched.uuid, name=sched.name, svm_name=sched.svm_name,
                    scope=sched.scope, schedule_type=sched.schedule_type, minutes=sched.minutes,
                    hours=sched.hours, days=sched.days, weekdays=sched.weekdays, last_seen_at=now,
                )
            )

    db.commit()


def _get_cluster_or_404(db: Session, cluster_id: str) -> NetAppCluster:
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    return cluster


def _discover_and_persist(db: Session, cluster: NetAppCluster) -> list:
    service = _service_for(cluster)
    steps, data = service.run_discovery()
    step_success = {s.step: s.success for s in steps}
    _persist_discovery(db, cluster, data, step_success)
    return steps


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
        cluster.ontap_cluster_name = summary.name
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
        ontap_cluster_name=summary.name,
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
    cluster = _get_cluster_or_404(db, cluster_id)
    return _refresh_status(db, cluster)


@router.post("/{cluster_id}/enroll-certificate", response_model=NetAppClusterRead)
def enroll_certificate(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> NetAppCluster:
    cluster = _get_cluster_or_404(db, cluster_id)
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
    cluster = _get_cluster_or_404(db, cluster_id)
    return _discover_and_persist(db, cluster)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)
    db.delete(cluster)
    db.commit()


@router.post("/{cluster_id}/igroups", status_code=status.HTTP_201_CREATED)
def create_igroup(
    cluster_id: str, payload: IgroupCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    """Legt nur die Initiator-Gruppe an -- loest KEINE Discovery aus. Aufrufer
    (Standalone-Formular oder der mehrstufige LUN-Anlegen-Workflow) entscheiden
    selbst, wann/ob im Anschluss neu discovert wird, damit der Fortschritt im
    Frontend Schritt fuer Schritt sichtbar bleibt statt in einer einzelnen
    Anfrage zu verschwinden."""
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.create_igroup(payload.svm_name, payload.name, payload.os_type, payload.protocol, payload.initiators)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created"}


@router.post("/{cluster_id}/volumes", status_code=status.HTTP_201_CREATED)
def create_volume(
    cluster_id: str, payload: VolumeCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.create_volume(
            payload.svm_name, payload.name, payload.aggregate_name, payload.size_bytes,
            security_style=payload.security_style, guarantee_type=payload.guarantee_type, volume_type=payload.volume_type,
        )
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created"}


@router.patch("/{cluster_id}/volumes/{volume_uuid}")
def update_volume(
    cluster_id: str, volume_uuid: str, payload: VolumeUpdate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.update_volume(volume_uuid, size_bytes=payload.size_bytes, state=payload.state)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "updated"}


@router.delete("/{cluster_id}/volumes/{volume_uuid}")
def delete_volume(
    cluster_id: str, volume_uuid: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.delete_volume(volume_uuid)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.post("/{cluster_id}/luns", status_code=status.HTTP_201_CREATED)
def create_lun(
    cluster_id: str, payload: LunCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    """Legt nur die LUN in einem (bereits existierenden) Volume an. Das
    Anlegen eines neuen Volumes ist ein eigener Schritt (POST .../volumes),
    den das Frontend bei Bedarf davor ausfuehrt -- dadurch kann der
    LUN-Anlegen-Workflow jeden Teilschritt einzeln als Fortschritt anzeigen."""
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.create_lun(
            payload.svm_name, payload.volume_name, payload.lun_name, payload.os_type, payload.size_bytes,
            space_allocation_enabled=payload.space_allocation_enabled,
        )
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created"}


@router.patch("/{cluster_id}/luns/{lun_uuid}")
def update_lun(
    cluster_id: str, lun_uuid: str, payload: LunUpdate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.update_lun(lun_uuid, size_bytes=payload.size_bytes, enabled=payload.enabled)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "updated"}


@router.delete("/{cluster_id}/luns/{lun_uuid}")
def delete_lun(
    cluster_id: str, lun_uuid: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.delete_lun(lun_uuid)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.post("/{cluster_id}/lun-maps", status_code=status.HTTP_201_CREATED)
def create_lun_map(
    cluster_id: str, payload: LunMapCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.create_lun_map(payload.svm_name, payload.lun_name, payload.igroup_name)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created"}


@router.delete("/{cluster_id}/lun-maps/{lun_uuid}")
def delete_lun_map(
    cluster_id: str, lun_uuid: str, igroup_name: str, svm_name: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.delete_lun_map(lun_uuid, igroup_name, svm_name)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.post("/{cluster_id}/snapmirror-policies", status_code=status.HTTP_201_CREATED)
def create_snapmirror_policy(
    cluster_id: str, payload: SnapmirrorPolicyCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.create_snapmirror_policy(
            payload.svm_name, payload.name, payload.vault_type, [r.model_dump() for r in payload.rules]
        )
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created"}


@router.patch("/{cluster_id}/snapmirror-policies/{policy_uuid}")
def update_snapmirror_policy(
    cluster_id: str, policy_uuid: str, payload: SnapmirrorPolicyUpdate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.update_snapmirror_policy(policy_uuid, [r.model_dump() for r in payload.rules])
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "updated"}


@router.post("/{cluster_id}/schedules", status_code=status.HTTP_201_CREATED)
def create_schedule(
    cluster_id: str, payload: ScheduleCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.create_schedule(payload.name, payload.svm_name, payload.minutes, payload.hours, payload.days, payload.weekdays)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created"}


@router.post("/{cluster_id}/snapmirror-relationships", status_code=status.HTTP_201_CREATED)
def create_snapmirror_relationship(
    cluster_id: str, payload: SnapmirrorRelationshipCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    """SnapMirror-Beziehungen werden immer von der Zielseite aus angelegt
    (cluster_id = Ziel-Cluster). Liegt die Quelle auf einem anderen
    registrierten Cluster, wird deren echter ONTAP-Cluster-Name (nicht
    unser Anzeigename) im 'source.cluster'-Feld referenziert."""
    destination_cluster = _get_cluster_or_404(db, cluster_id)
    source_cluster = _get_cluster_or_404(db, payload.source_cluster_id)
    service = _service_for(destination_cluster)
    source_cluster_name = source_cluster.ontap_cluster_name if source_cluster.id != destination_cluster.id else None
    try:
        uuid = service.create_snapmirror_relationship(
            f"{payload.source_svm_name}:{payload.source_volume_name}",
            f"{payload.destination_svm_name}:{payload.destination_volume_name}",
            payload.policy_name,
            schedule_name=payload.schedule_name,
            source_cluster_name=source_cluster_name,
        )
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created", "uuid": uuid}


@router.patch("/{cluster_id}/snapmirror-relationships/{relationship_uuid}")
def update_snapmirror_relationship(
    cluster_id: str, relationship_uuid: str, payload: SnapmirrorRelationshipUpdate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.update_snapmirror_relationship(relationship_uuid, policy_name=payload.policy_name, schedule_name=payload.schedule_name)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "updated"}


@router.post("/{cluster_id}/snapmirror-relationships/{relationship_uuid}/initialize", status_code=status.HTTP_202_ACCEPTED)
def initialize_snapmirror_relationship(
    cluster_id: str, relationship_uuid: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        service.initialize_snapmirror_relationship(relationship_uuid)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "initialized"}


@router.post("/{cluster_id}/cluster-peers", status_code=status.HTTP_201_CREATED)
def create_cluster_peer(
    cluster_id: str, payload: ClusterPeerCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    """Peert diesen Cluster mit einem anderen bereits registrierten Cluster.
    Beide Seiten muessen in unserer App registriert sein, da fuer den
    ONTAP-Peering-Workflow (Passphrase erzeugen -> auf der Gegenseite mit den
    Intercluster-LIF-Adressen annehmen) Zugangsdaten fuer BEIDE Cluster
    benoetigt werden -- vergleichbar mit 'cluster peer create
    -generate-passphrase' gefolgt von 'cluster peer create -peer-addrs ...'
    auf der Gegenseite."""
    cluster_a = _get_cluster_or_404(db, cluster_id)
    cluster_b = _get_cluster_or_404(db, payload.peer_cluster_id)
    service_a = _service_for(cluster_a)
    service_b = _service_for(cluster_b)
    try:
        passphrase, a_local_ips = service_a.generate_cluster_peer_passphrase()
        service_b.accept_cluster_peer(a_local_ips, passphrase)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _discover_and_persist(db, cluster_a)
    _discover_and_persist(db, cluster_b)
    return {"status": "peered"}


@router.post("/{cluster_id}/svm-peers", status_code=status.HTTP_201_CREATED)
def create_svm_peer(
    cluster_id: str, payload: SvmPeerCreate, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> dict:
    """Erstellt eine SVM-Peer-Beziehung zwischen einer SVM auf diesem Cluster
    und einer SVM auf einem bereits (Cluster-)gepeerten, in unserer App
    registrierten Cluster. Die Anfrage wird auf der Gegenseite automatisch
    angenommen, da wir dort ebenfalls Zugangsdaten besitzen."""
    cluster_local = _get_cluster_or_404(db, cluster_id)
    cluster_remote = _get_cluster_or_404(db, payload.peer_cluster_id)
    service_local = _service_for(cluster_local)
    service_remote = _service_for(cluster_remote)
    if not cluster_remote.ontap_cluster_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Der reale ONTAP-Cluster-Name der Gegenseite ist noch nicht bekannt -- zuerst 'Verbindung erneut prüfen' ausführen.",
        )
    try:
        service_local.create_svm_peer(
            payload.local_svm_name, cluster_remote.ontap_cluster_name, payload.peer_svm_name, payload.applications
        )
        service_remote.accept_pending_svm_peer(payload.peer_svm_name, payload.local_svm_name)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _discover_and_persist(db, cluster_local)
    _discover_and_persist(db, cluster_remote)
    return {"status": "peered"}
