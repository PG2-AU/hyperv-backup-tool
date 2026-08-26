"""Restore-Setup-Wizard: richtet die Infrastruktur ein, die der VHDX-Restore-
Workflow braucht (siehe docs/restore-Untersuchung) -- der Container meldet
sich per iSCSI direkt bei der NetApp-SVM an, klont dort eine LUN aus einem
Snapshot und kopiert die wiederhergestellte VHDX per SMB auf die Ziel-CSV.

Dieser Workflow lief bewusst NICHT ueber den Hyper-V-Cluster (Windows
Failover Clustering blockiert node-lokal jede neu sichtbare, block-identische
Disk, siehe Chat-Verlauf) und NICHT ueber den Host der WSL/Podman-Umgebung
(kein Routing zum iSCSI-Datennetz) -- sondern ueber ein dediziertes,
erreichbares iSCSI-Interface auf der SVM plus einen eigenen Initiator im
Container selbst.

Zwei Kategorien von Anforderungen:
1. Pakete (iscsi-initiator-utils, ntfs-3g, cifs-utils/samba-client, kpartx,
   parted) -- koennen zur Laufzeit im Container nachinstalliert werden
   (siehe /requirements/install), sind aber auch im Dockerfile fest
   verankert (docker/Dockerfile), damit ein frisches Image sie von Anfang
   an mitbringt.
2. Container-Rechte (CAP_SYS_ADMIN + Blockgeraete-Zugriff fuer iSCSI-Logins
   und Mount-Operationen) -- KANN NICHT zur Laufzeit nachgeruestet werden,
   sondern erfordert eine einmalige Anpassung der Container-Startparameter
   (siehe REQUIRED_CAPABILITIES_HINT unten).
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster
from app.models.restore_infra import RestoreInfraConfig
from app.schemas.netapp_cluster import DiscoveryStepRead
from app.services.netapp_service import NetAppConnectionError, NetAppOntapService, tcp_port_open
from app.core.crypto import decrypt_secret

router = APIRouter(prefix="/api/restore-infra", tags=["restore-infra"])

INITIATOR_NAME_PATH = Path("/etc/iscsi/initiatorname.iscsi")

REQUIRED_BINARIES = [
    ("iscsiadm", "iSCSI-Initiator (iscsi-initiator-utils)"),
    ("ntfs-3g", "NTFS/CSVFS-Lesezugriff (ntfs-3g)"),
    ("smbclient", "SMB-Kopie auf die Hyper-V-CSV (samba-client)"),
    ("kpartx", "Partitionserkennung (kpartx)"),
    ("partprobe", "Partitionstabellen-Tool (parted)"),
]

REQUIRED_CAPABILITIES_HINT = (
    "Der Container braucht zusaetzlich CAP_SYS_ADMIN sowie Zugriff auf neu "
    "erscheinende Blockgeraete fuer echte iSCSI-Logins und Mount-Operationen. "
    "Das kann nicht zur Laufzeit nachgeruestet werden -- der Container muss "
    "einmalig mit zusaetzlichen Rechten neu erstellt werden, z.B.: "
    "podman run ... --cap-add=SYS_ADMIN --device /dev/fuse "
    "--device-cgroup-rule='b 8:* rmw' ..."
)


class RequirementCheck(BaseModel):
    name: str
    label: str
    satisfied: bool
    detail: str | None = None


class RequirementsStatus(BaseModel):
    checks: list[RequirementCheck]
    all_packages_ok: bool
    capability_ok: bool
    capability_hint: str


class InitiatorInfo(BaseModel):
    configured: bool
    iqn: str | None = None


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


def _check_mount_capability() -> RequirementCheck:
    try:
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            result = subprocess.run(["mount", "--bind", src, dst], capture_output=True, text=True, timeout=10)
            ok = result.returncode == 0
            if ok:
                subprocess.run(["umount", dst], capture_output=True, timeout=10)
            return RequirementCheck(
                name="mount_capability",
                label="Container-Berechtigung fuer Mount-Operationen (CAP_SYS_ADMIN)",
                satisfied=ok,
                detail=None if ok else (result.stderr or "").strip() or "Mount wurde vom Container abgelehnt.",
            )
    except Exception as exc:
        return RequirementCheck(
            name="mount_capability", label="Container-Berechtigung fuer Mount-Operationen (CAP_SYS_ADMIN)",
            satisfied=False, detail=str(exc),
        )


@router.get("/requirements", response_model=RequirementsStatus)
def get_requirements(user=Depends(require_permission(Permission.STORAGE_MANAGE))) -> RequirementsStatus:
    checks = [
        RequirementCheck(name=binname, label=label, satisfied=shutil.which(binname) is not None)
        for binname, label in REQUIRED_BINARIES
    ]
    capability_check = _check_mount_capability()
    checks.append(capability_check)
    return RequirementsStatus(
        checks=checks,
        all_packages_ok=all(c.satisfied for c in checks if c.name != "mount_capability"),
        capability_ok=capability_check.satisfied,
        capability_hint=REQUIRED_CAPABILITIES_HINT,
    )


@router.post("/requirements/install", response_model=list[DiscoveryStepRead])
def install_requirements(user=Depends(require_permission(Permission.STORAGE_MANAGE))) -> list[DiscoveryStepRead]:
    steps: list[DiscoveryStepRead] = []

    def run_step(step_id: str, cmd: list[str], timeout: int = 180) -> None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            success = proc.returncode == 0
            message = "OK" if success else ((proc.stderr or proc.stdout or "").strip()[:500] or "Fehlgeschlagen")
        except Exception as exc:
            success = False
            message = str(exc)
        steps.append(DiscoveryStepRead(step=step_id, success=success, message=message))

    run_step("epel", ["dnf", "install", "-y", "epel-release"])
    run_step(
        "packages",
        ["dnf", "install", "-y", "iscsi-initiator-utils", "cifs-utils", "samba-client", "ntfs-3g", "kpartx", "parted"],
    )

    # In diesem minimalen Container-Image laeuft das %post-Scriptlet des
    # iscsi-initiator-utils-Pakets nicht zuverlaessig (erzeugt normalerweise
    # /etc/iscsi/initiatorname.iscsi automatisch) -- gegen den echten
    # Container verifiziert: Paket installiert, Datei fehlte trotzdem. Wird
    # hier explizit nachgeholt, damit der Initiator garantiert existiert.
    if not INITIATOR_NAME_PATH.exists() and shutil.which("iscsi-iname"):
        try:
            iqn = subprocess.run(["iscsi-iname"], capture_output=True, text=True, timeout=10).stdout.strip()
            INITIATOR_NAME_PATH.parent.mkdir(parents=True, exist_ok=True)
            INITIATOR_NAME_PATH.write_text(f"InitiatorName={iqn}\n")
            steps.append(DiscoveryStepRead(step="initiator", success=True, message=f"Initiator erzeugt: {iqn}"))
        except Exception as exc:
            steps.append(DiscoveryStepRead(step="initiator", success=False, message=str(exc)))
    elif INITIATOR_NAME_PATH.exists():
        steps.append(DiscoveryStepRead(step="initiator", success=True, message="Initiator bereits vorhanden"))
    else:
        steps.append(DiscoveryStepRead(step="initiator", success=False, message="iscsi-iname nicht gefunden (Paketinstallation pruefen)"))

    if shutil.which("iscsid"):
        run_step("iscsid", ["iscsid"], timeout=15)
    else:
        steps.append(DiscoveryStepRead(step="iscsid", success=False, message="iscsid nicht gefunden (Paketinstallation pruefen)"))

    return steps


def _read_initiator() -> InitiatorInfo:
    if not INITIATOR_NAME_PATH.exists():
        return InitiatorInfo(configured=False)
    for line in INITIATOR_NAME_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("InitiatorName="):
            return InitiatorInfo(configured=True, iqn=line.split("=", 1)[1])
    return InitiatorInfo(configured=False)


@router.get("/initiator", response_model=InitiatorInfo)
def get_initiator(user=Depends(require_permission(Permission.STORAGE_MANAGE))) -> InitiatorInfo:
    return _read_initiator()


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
    testet fuer jedes, ob Port 3260 (iSCSI) vom Container aus erreichbar
    ist -- ohne diesen Test wuerde man erst beim eigentlichen Restore-Lauf
    merken, dass ein Interface im falschen Netz liegt."""
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
    """Letzter Wizard-Schritt: legt die iSCSI-Zugriffsberechtigung (sonst
    'authorization failure' schon bei der Discovery, siehe echte DEMO7-SVM)
    und die Igroup fuer den Container-Initiator an, und speichert die
    Konfiguration fuer spaetere Restore-Laeufe."""
    cluster = _get_cluster_or_404(db, cluster_id)
    initiator = _read_initiator()
    if not initiator.configured or not initiator.iqn:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kein iSCSI-Initiator konfiguriert (Anforderungen zuerst installieren).",
        )

    service = _service_for(cluster)
    try:
        service.ensure_iscsi_credentials(payload.svm_name, initiator.iqn, auth_type="none")
        service.ensure_igroup_initiator(payload.svm_name, payload.igroup_name, os_type="linux", initiator_iqn=initiator.iqn)
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
        existing.initiator_iqn = initiator.iqn
        config = existing
    else:
        config = RestoreInfraConfig(
            netapp_cluster_id=cluster_id, svm_name=payload.svm_name,
            iscsi_lif_name=payload.iscsi_lif_name, iscsi_lif_address=payload.iscsi_lif_address,
            iscsi_lif_port=payload.iscsi_lif_port, igroup_name=payload.igroup_name, initiator_iqn=initiator.iqn,
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
