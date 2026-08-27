"""Restore-Setup-Wizard: richtet die NetApp-seitige Infrastruktur ein, die
der VHDX-Restore-Workflow braucht -- der Restore-Proxy-Host (ein dedizierter
Windows-Host, siehe HVNB_RESTORE_PROXY_*) meldet sich per nativem
Windows-iSCSI-Initiator an der Ziel-SVM an, klont dort eine LUN aus einem
Snapshot und kopiert die wiederhergestellte VHDX per SMB auf die Ziel-CSV.

Fruehere Version lief ueber einen iSCSI-Initiator IM CONTAINER selbst
(Linux, iscsiadm) -- das scheiterte strukturell an mehreren, gegen die echte
Zielumgebung verifizierten Problemen (siehe Chat-Verlauf: rootless Podman
ohne echtes CAP_SYS_ADMIN, WSL2-Kernel-Netlink-Inkompatibilitaeten, kein
devtmpfs). Der Initiator lebt jetzt auf dem Windows-Proxy-Host; dieser
Router fragt ihn per WinRM ab, statt eine lokale Datei im Container zu
lesen -- Paket-/Capability-Checks (frueher hier) entfallen dadurch
komplett."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster
from app.models.restore_infra import RestoreInfraConfig
from app.services.hyperv_service import HyperVConnectionError, HyperVService
from app.services.netapp_service import NetAppConnectionError, NetAppOntapService, tcp_port_open
from app.core.crypto import decrypt_secret

router = APIRouter(prefix="/api/restore-infra", tags=["restore-infra"])


class InitiatorInfo(BaseModel):
    configured: bool
    iqn: str | None = None
    error: str | None = None


class LifCandidate(BaseModel):
    name: str
    address: str
    reachable: bool


class BroadcastDomainPortRead(BaseModel):
    node_name: str
    port_name: str


class BroadcastDomainRead(BaseModel):
    name: str
    ipspace: str
    ports: list[BroadcastDomainPortRead]


class CreateLifRequest(BaseModel):
    svm_name: str
    name: str
    address: str
    netmask: str
    broadcast_domain: str
    home_node: str
    home_port: str


class SetupRequest(BaseModel):
    svm_name: str
    iscsi_lif_name: str | None = None
    iscsi_lif_address: str
    iscsi_lif_port: int = 3260
    igroup_name: str = "hvnb_restore"


class RestoreInfraConfigRead(BaseModel):
    id: str
    netapp_cluster_id: str
    svm_name: str
    iscsi_lif_name: str | None = None
    iscsi_lif_address: str
    iscsi_lif_port: int
    igroup_name: str
    initiator_iqn: str

    class Config:
        from_attributes = True


def _proxy_service_and_session():
    """Verbindet zum Restore-Proxy-Host (WinRM). Wirft HTTPException, falls
    nicht konfiguriert oder nicht erreichbar -- wird von mehreren Routen
    dieses Wizards gebraucht (Initiator lesen, spaeter Setup)."""
    settings = get_settings()
    if not settings.restore_proxy_address or not settings.restore_proxy_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Kein Restore-Proxy-Host konfiguriert (HVNB_RESTORE_PROXY_ADDRESS/"
                "HVNB_RESTORE_PROXY_USERNAME/HVNB_RESTORE_PROXY_PASSWORD in der .env)."
            ),
        )
    service = HyperVService(settings, settings.restore_proxy_address, use_https=settings.winrm_use_https)
    try:
        session = service.connect(settings.restore_proxy_username, settings.restore_proxy_password, read_timeout_sec=15, operation_timeout_sec=10)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Restore-Proxy-Host nicht erreichbar: {exc}") from exc
    return service, session


@router.get("/initiator", response_model=InitiatorInfo)
def get_initiator(user=Depends(require_permission(Permission.STORAGE_MANAGE))) -> InitiatorInfo:
    """Fragt die iSCSI-Initiator-IQN des Restore-Proxy-Hosts per WinRM ab
    (startet dabei bei Bedarf den MSiSCSI-Dienst -- auf einem frischen
    Windows Server ist er standardmaessig deaktiviert)."""
    try:
        service, session = _proxy_service_and_session()
        iqn = service.get_initiator_iqn(session)
    except HTTPException as exc:
        return InitiatorInfo(configured=False, error=exc.detail)
    except (HyperVConnectionError, RuntimeError) as exc:
        return InitiatorInfo(configured=False, error=str(exc))
    return InitiatorInfo(configured=True, iqn=iqn)


def _get_cluster_or_404(db: Session, cluster_id: str) -> NetAppCluster:
    cluster = db.get(NetAppCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    return cluster


def _service_for(cluster: NetAppCluster) -> NetAppOntapService:
    if cluster.auth_method == NetAppAuthMethod.CERTIFICATE and cluster.client_cert_path and cluster.client_key_path:
        return NetAppOntapService(
            host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
            cert_path=cluster.client_cert_path, key_path=cluster.client_key_path,
        )
    return NetAppOntapService(
        host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
        username=cluster.username,
        password=decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else None,
    )


@router.get("/clusters/{cluster_id}/svms/{svm_name}/lifs", response_model=list[LifCandidate])
def check_svm_lifs(
    cluster_id: str, svm_name: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> list[LifCandidate]:
    """Listet die bekannten (discovered) Netzwerk-Interfaces der SVM und
    testet fuer jedes, ob Port 3260 (iSCSI) vom Backend-Container aus
    erreichbar ist -- reiner Netzwerk-Sichtbarkeitstest, unabhaengig vom
    eigentlichen (auf dem Proxy-Host laufenden) iSCSI-Login."""
    from app.models.netapp_discovery import NetAppNetworkInterface

    _get_cluster_or_404(db, cluster_id)
    interfaces = (
        db.query(NetAppNetworkInterface)
        .filter(NetAppNetworkInterface.cluster_id == cluster_id, NetAppNetworkInterface.svm_name == svm_name)
        .all()
    )
    return [
        LifCandidate(name=ni.name or "", address=ni.address or "", reachable=tcp_port_open(ni.address, 3260, timeout_sec=2.0))
        for ni in interfaces
        if ni.address
    ]


@router.get("/clusters/{cluster_id}/broadcast-domains", response_model=list[BroadcastDomainRead])
def list_broadcast_domains(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> list[BroadcastDomainRead]:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        domains = service.list_broadcast_domains()
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [
        BroadcastDomainRead(
            name=d.name, ipspace=d.ipspace,
            ports=[BroadcastDomainPortRead(node_name=p.node_name, port_name=p.port_name) for p in d.ports],
        )
        for d in domains
    ]


@router.post("/clusters/{cluster_id}/lif", response_model=LifCandidate, status_code=status.HTTP_201_CREATED)
def create_lif(
    cluster_id: str, payload: CreateLifRequest, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> LifCandidate:
    cluster = _get_cluster_or_404(db, cluster_id)
    service = _service_for(cluster)
    try:
        lif = service.create_iscsi_lif(
            svm_name=payload.svm_name, name=payload.name, address=payload.address, netmask=payload.netmask,
            broadcast_domain=payload.broadcast_domain, home_node=payload.home_node, home_port=payload.home_port,
        )
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LifCandidate(name=lif.name, address=lif.address, reachable=tcp_port_open(lif.address, 3260, timeout_sec=3.0))


@router.post("/clusters/{cluster_id}/setup", response_model=RestoreInfraConfigRead)
def setup_restore_infra(
    cluster_id: str, payload: SetupRequest, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> RestoreInfraConfig:
    """Letzter Wizard-Schritt: liest die aktuelle Initiator-IQN frisch vom
    Restore-Proxy-Host, legt die iSCSI-Zugriffsberechtigung (sonst
    'authorization failure' schon bei der Discovery, siehe echte DEMO7-SVM)
    und die Igroup dafuer an, und speichert die Konfiguration fuer spaetere
    Restore-Laeufe."""
    cluster = _get_cluster_or_404(db, cluster_id)
    proxy_service, proxy_session = _proxy_service_and_session()
    try:
        iqn = proxy_service.get_initiator_iqn(proxy_session)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    service = _service_for(cluster)
    try:
        service.ensure_iscsi_credentials(payload.svm_name, iqn, auth_type="none")
        service.ensure_igroup_initiator(payload.svm_name, payload.igroup_name, os_type="windows", initiator_iqn=iqn)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    existing = (
        db.query(RestoreInfraConfig)
        .filter(RestoreInfraConfig.netapp_cluster_id == cluster_id, RestoreInfraConfig.svm_name == payload.svm_name)
        .first()
    )
    if existing:
        existing.iscsi_lif_name = payload.iscsi_lif_name
        existing.iscsi_lif_address = payload.iscsi_lif_address
        existing.iscsi_lif_port = payload.iscsi_lif_port
        existing.igroup_name = payload.igroup_name
        existing.initiator_iqn = iqn
        config = existing
    else:
        config = RestoreInfraConfig(
            netapp_cluster_id=cluster_id, svm_name=payload.svm_name,
            iscsi_lif_name=payload.iscsi_lif_name, iscsi_lif_address=payload.iscsi_lif_address,
            iscsi_lif_port=payload.iscsi_lif_port, igroup_name=payload.igroup_name, initiator_iqn=iqn,
        )
        db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/configs", response_model=list[RestoreInfraConfigRead])
def list_configs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE))) -> list[RestoreInfraConfig]:
    return db.query(RestoreInfraConfig).all()


@router.delete("/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(
    config_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.STORAGE_MANAGE)),
) -> None:
    config = db.get(RestoreInfraConfig, config_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Konfiguration nicht gefunden")
    db.delete(config)
    db.commit()
