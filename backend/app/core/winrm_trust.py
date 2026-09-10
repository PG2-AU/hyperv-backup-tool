"""Aufloesung des CA-Trust-Pfads fuer die WinRM-HTTPS-Verbindungen zu den
Hyper-V-Hosts.

Hintergrund: `HyperVService._session()` baut fuer JEDE WinRM-Operation eine
frische `winrm.Session`, und `requests` liest die als `ca_trust_path`
uebergebene Datei bei JEDER Verbindung neu von der Platte. Nur der
Pfad-*String* stammt aus dem `@lru_cache`-ten `Settings`
(`winrm_ca_trust_path`, aus `HVNB_WINRM_CA_TRUST_PATH`). Eine Bundle-Datei
an einem festen Pfad zu ueberschreiben wirkt daher sofort beim naechsten
Verbindungsaufbau -- ohne Neustart.

Die GUI-Sektion "Settings > WinRM-Zertifikate" schreibt das aus den
hochgeladenen Host-Zertifikaten erzeugte Bundle nach
`<winrm_trust_dir>/bundle.pem`. Damit das auch bei einer Erstinstallation
ohne gesetztes `HVNB_WINRM_CA_TRUST_PATH` greift (dort war der Pfad beim
Boot leer -> Session nutzt nur den System-Truststore), faellt
`resolved_trust_path()` auf dieses verwaltete Bundle zurueck, sobald die
Datei existiert. Ein explizit gesetztes `HVNB_WINRM_CA_TRUST_PATH` hat
weiterhin Vorrang -- bestehende Installationen (z.B. mit
`/etc/hvnb/certs/winrm-ca.pem`) schreiben und lesen unveraendert ihre
bisherige Datei.
"""

from pathlib import Path

from app.core.config import Settings

MANAGED_BUNDLE_FILENAME = "bundle.pem"

# pywinrm-Sentinel: nur der System-Truststore, keine zusaetzliche CA/Datei.
LEGACY_REQUESTS = "legacy_requests"


def managed_bundle_path(settings: Settings) -> Path:
    """Fester, von der GUI verwalteter Bundle-Pfad."""
    return Path(settings.winrm_trust_dir) / MANAGED_BUNDLE_FILENAME


def bundle_write_target(settings: Settings) -> Path:
    """Wohin `build-bundle` schreibt: ein explizit gesetztes
    `HVNB_WINRM_CA_TRUST_PATH` wird 1:1 bedient (bestehende Installationen
    behalten ihre Datei), sonst der verwaltete Pfad."""
    if settings.winrm_ca_trust_path:
        return Path(settings.winrm_ca_trust_path)
    return managed_bundle_path(settings)


def resolved_trust_path(settings: Settings) -> str:
    """Was `HyperVService._session()` als `ca_trust_path` uebergibt:
    explizites ENV zuerst, sonst das verwaltete Bundle sofern vorhanden,
    sonst der pywinrm-Default (nur System-Truststore)."""
    if settings.winrm_ca_trust_path:
        return settings.winrm_ca_trust_path
    managed = managed_bundle_path(settings)
    if managed.is_file():
        return str(managed)
    return LEGACY_REQUESTS
