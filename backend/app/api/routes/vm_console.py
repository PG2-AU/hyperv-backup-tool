"""Remote-Sitzung auf eine VM (Backlog #75, Stufe "Datei-Download",
Nutzer-Freigabe 2026-10-02 nach erfolgreichem Vorab-Test): die App erzeugt
.rdp-Dateien fuer den Remotedesktop-Client des Benutzers --

- "Konsole": Verbindung zum Hyper-V-Knoten auf Port 2179 mit der VM-ID als
  Preconnection-Blob, wie es VMConnect intern macht. Zeigt den Bildschirm
  der VM, funktioniert ohne Netzwerk im Gast (Installation, Boot, BIOS).
  Der PC des Benutzers muss den Knoten auf Port 2179 erreichen, und das
  dort angegebene Konto braucht Hyper-V-Rechte auf dem Host.
- "RDP ins Gastsystem": Verbindung zur IP-Adresse, die der Gast ueber die
  Integrationsdienste meldet (nur Windows-Gaeste mit aktiviertem RDP).

Die App selbst baut KEINE Sitzung auf und gibt keine Zugangsdaten weiter --
angemeldet wird im Remotedesktop-Client. Eigenes Recht vm:console; jeder
Download steht im System-Log."""

import ipaddress
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVVm
from app.models.system_log import SystemLogEvent
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/vm-console", tags=["vm-console"])

CONSOLE_PORT = 2179


class VmConsoleInfo(BaseModel):
    cluster_id: str
    vm_name: str
    vm_id: str
    state: str
    host: str
    console_port: int = CONSOLE_PORT
    # IPv4 zuerst; Link-Local/APIPA ausgefiltert.
    ip_addresses: list[str]


def _usable_ips(addresses: list[str]) -> list[str]:
    result = []
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(str(raw).strip())
        except ValueError:
            continue
        if ip.is_link_local or ip.is_loopback or ip.is_unspecified:
            continue
        result.append(ip)
    return [str(ip) for ip in sorted(result, key=lambda i: (i.version, int(i)))]


def _load(db: Session, cluster_id: str, vm_name: str) -> VmConsoleInfo:
    cluster = db.get(HyperVCluster, cluster_id)
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if cluster is None or vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
    settings = get_settings()
    password = decrypt_secret(cluster.encrypted_password)
    try:
        cno = HyperVService(
            settings, cluster.management_address, use_https=cluster.use_https, node_hostname=cluster.hyperv_cluster_name,
        )
        cno_session = cno.connect(cluster.username, password, read_timeout_sec=30, operation_timeout_sec=20)
        owner = cno.get_vm_owner_node(cno_session, vm_name) or vm.host_name
        if not owner:
            raise RuntimeError("Der Knoten der VM ist nicht bekannt -- bitte Discovery ausführen")
        node = HyperVService(settings, cno.resolve_node_address(cno_session, owner), use_https=cluster.use_https, node_hostname=owner)
        info = node.vm_console_info(node.connect(cluster.username, password), vm_name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc
    return VmConsoleInfo(
        cluster_id=cluster_id, vm_name=vm_name, vm_id=info["vm_id"] or (vm.vm_uuid or ""), state=info["state"], host=owner,
        ip_addresses=_usable_ips(info["ip_addresses"]),
    )


def _rdp_response(lines: list[str], filename: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
    return Response(
        content="\r\n".join(lines) + "\r\n", media_type="application/x-rdp",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


def _log(db: Session, user, message: str) -> None:
    db.add(SystemLogEvent(level="INFO", source="vm-console", message=f"{message} (durch {user.display_name or user.username})"))
    db.commit()


@router.get("/{cluster_id}/{vm_name}", response_model=VmConsoleInfo)
def get_info(
    cluster_id: str, vm_name: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.VM_CONSOLE)),
) -> VmConsoleInfo:
    """Live: Knoten, auf dem die VM gerade laeuft, VM-ID, Status, Gast-IPs."""
    return _load(db, cluster_id, vm_name)


@router.get("/{cluster_id}/{vm_name}/console.rdp")
def console_rdp(
    cluster_id: str, vm_name: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.VM_CONSOLE)),
) -> Response:
    info = _load(db, cluster_id, vm_name)
    if not info.vm_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Die VM-ID ist nicht bekannt.")
    _log(db, user, f"Remote-Sitzung: Konsole von VM '{vm_name}' angefordert (Knoten {info.host}:{CONSOLE_PORT})")
    return _rdp_response(
        [
            f"full address:s:{info.host}",
            f"server port:i:{CONSOLE_PORT}",
            f"pcb:s:{info.vm_id.upper()}",  # Schreibweise wie im live geprueften Vorab-Test
            "negotiate security layer:i:0",
            "prompt for credentials:i:1",
        ],
        f"{vm_name}-Konsole.rdp",
    )


@router.get("/{cluster_id}/{vm_name}/guest.rdp")
def guest_rdp(
    cluster_id: str, vm_name: str, address: str, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.VM_CONSOLE)),
) -> Response:
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
    if vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültige IP-Adresse.") from exc
    _log(db, user, f"Remote-Sitzung: RDP ins Gastsystem von VM '{vm_name}' angefordert ({ip})")
    return _rdp_response([f"full address:s:{ip}", "prompt for credentials:i:1"], f"{vm_name}-RDP.rdp")
