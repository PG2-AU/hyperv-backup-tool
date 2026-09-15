"""Settings > Kerberos: globales Realm/KDC-Paar fuer
HVNB_WINRM_TRANSPORT=kerberos (Backlog-Punkt 50).

Bewusst genau EIN Realm/KDC statt pro Cluster, siehe app.core.
kerberos_config fuer die Begruendung. "Automatisch erkennen" nutzt die
Zugangsdaten eines bereits registrierten Clusters ueber dessen AKTUELL
funktionierenden Transport (NTLM/CredSSP), um Realm/KDC live abzufragen
-- derselbe nltest-basierte Check, der schon am 2026-09-14 manuell gegen
svaudemo7-hv1 verifiziert wurde (Realm HYPERV.DEMO.AU.LOCAL, KDC
svaudemo7-dc1.hyperv.demo.au.local)."""

import copy
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.routes.hyperv_clusters import _get_cluster_or_404, _service_for
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.kerberos_auth import KerberosTicketError, ensure_ticket
from app.core.kerberos_config import write_krb5_conf
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.kerberos_config import KerberosConfig
from app.schemas.kerberos_config import (
    KerberosConfigRead,
    KerberosConfigWrite,
    KerberosDetectRequest,
    KerberosDetectResult,
    KerberosTestRequest,
    KerberosTestResult,
)
from app.services.hyperv_service import HyperVConnectionError, HyperVService

router = APIRouter(prefix="/api/kerberos-config", tags=["kerberos-config"])


def _get_or_create_config(db: Session) -> KerberosConfig:
    config = db.query(KerberosConfig).first()
    if config is None:
        config = KerberosConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.get("", response_model=KerberosConfigRead)
def get_config(db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE))) -> KerberosConfig:
    return _get_or_create_config(db)


@router.put("", response_model=KerberosConfigRead)
def update_config(
    payload: KerberosConfigWrite, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> KerberosConfig:
    settings = get_settings()
    write_krb5_conf(settings, payload.realm, payload.kdc_hostname)
    config = _get_or_create_config(db)
    config.realm = payload.realm.strip().upper()
    config.kdc_hostname = payload.kdc_hostname.strip()
    config.kdc_address = payload.kdc_address
    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = getattr(user, "username", None)
    db.commit()
    db.refresh(config)
    return config


@router.post("/detect", response_model=KerberosDetectResult)
def detect_realm(
    payload: KerberosDetectRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> KerberosDetectResult:
    """Fragt Realm/KDC live bei einem bereits registrierten Cluster ab --
    ueber dessen AKTUELL konfigurierten Transport (NTLM/CredSSP, noch
    nicht Kerberos, das gibt es ja erst nach dieser Einrichtung).
    Liefert nur einen Vorschlag zur Bestaetigung, speichert nichts."""
    cluster = _get_cluster_or_404(db, payload.cluster_id)
    service = _service_for(cluster)
    password = decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else ""
    try:
        session = service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        script = (
            "$domain = (Get-WmiObject Win32_ComputerSystem).Domain; "
            "$dcInfo = (nltest /dsgetdc:$domain 2>&1 | Out-String); "
            "$dcMatch = [regex]::Match($dcInfo, 'DC:\\s*\\\\\\\\(\\S+)'); "
            "$addrMatch = [regex]::Match($dcInfo, 'Address:\\s*\\\\\\\\(\\S+)'); "
            "[PSCustomObject]@{ "
            "Realm = $domain.ToUpper(); "
            "KdcHostname = $(if ($dcMatch.Success) { $dcMatch.Groups[1].Value } else { $null }); "
            "KdcAddress = $(if ($addrMatch.Success) { $addrMatch.Groups[1].Value } else { $null }) "
            "} | ConvertTo-Json"
        )
        result = service._run_ps(session, script)  # noqa: SLF001 -- gleicher Zugriff wie an vielen anderen Stellen dieser Routen
    except Exception as exc:  # noqa: BLE001 -- WinRM-Transportfehler sind keine einheitliche Exception-Klasse
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cluster nicht erreichbar: {exc}") from exc
    if not result.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Realm/KDC-Erkennung fehlgeschlagen: {result.error}")
    try:
        data = json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unerwartete Antwort: {result.output}") from exc
    if not data.get("Realm") or not data.get("KdcHostname"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Realm oder KDC konnten nicht ermittelt werden (nltest lieferte kein verwertbares Ergebnis).",
        )
    return KerberosDetectResult(realm=data["Realm"], kdc_hostname=data["KdcHostname"], kdc_address=data.get("KdcAddress"))


@router.post("/test", response_model=KerberosTestResult)
def test_connection(
    payload: KerberosTestRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> KerberosTestResult:
    """Testet Ticket-Beschaffung + eine echte WinRM-Verbindung gegen den
    Cluster -- schreibt dafuer dieselbe krb5.conf wie 'Speichern' (siehe
    Modul-Docstring), unabhaengig vom aktuell in Settings gesetzten
    HVNB_WINRM_TRANSPORT (der Test erzwingt Kerberos fuer diesen einen
    Aufruf, wie beim NTLM-Test vom 2026-09-13 -- settings-Kopie mit
    ueberschriebenem Transport statt einer globalen Aenderung)."""
    cluster = _get_cluster_or_404(db, payload.cluster_id)
    base_settings = get_settings()
    write_krb5_conf(base_settings, payload.realm, payload.kdc_hostname)
    settings = copy.copy(base_settings)
    settings.winrm_transport = "kerberos"
    service = HyperVService(
        settings, cluster.management_address, use_https=cluster.use_https, node_hostname=cluster.hyperv_cluster_name,
    )
    password = decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else ""
    try:
        ensure_ticket(cluster.username, password, payload.realm)
    except KerberosTicketError as exc:
        return KerberosTestResult(success=False, message=str(exc))
    try:
        session = service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
        result = service._run_ps(session, "$env:COMPUTERNAME")  # noqa: SLF001
    except HyperVConnectionError as exc:
        return KerberosTestResult(success=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 -- WinRM-Transportfehler sind keine einheitliche Exception-Klasse
        return KerberosTestResult(success=False, message=str(exc))
    if not result.success:
        return KerberosTestResult(success=False, message=result.error or "Verbindung fehlgeschlagen")
    return KerberosTestResult(success=True, message=f"Verbunden mit '{result.output.strip()}'")
