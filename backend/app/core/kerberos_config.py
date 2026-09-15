"""Verwaltung der krb5.conf fuer HVNB_WINRM_TRANSPORT=kerberos
(Backlog-Punkt 50).

Bewusst genau EIN Realm/KDC statt pro Cluster -- das Tool registriert
laut Nutzer-Vorgabe ohnehin nur Cluster innerhalb einer AD-Domaene, eine
urspruenglich angedachte Multi-Domain-Faehigkeit wurde explizit
gestrichen.

Gleiches Muster wie app.core.winrm_trust: die Datei liegt an einem
festen Pfad auf dem persistenten /data-Volume, wird bei jedem Ticket-
Erwerb (app.core.kerberos_auth) frisch von libkrb5 gelesen -- eine
Aenderung ueber "Settings > Kerberos" wirkt sofort, ohne Container-
Neustart. Nur die KRB5_CONFIG-Umgebungsvariable (die auf diesen festen
Pfad zeigt) muss einmalig beim App-Start gesetzt werden, siehe
ensure_krb5_config_env() in app.main.
"""

import os
from pathlib import Path

from app.core.config import Settings

MANAGED_KRB5_CONF_FILENAME = "krb5.conf"
# Kleine Sidecar-Datei, die NUR den Realm-Namen enthaelt -- vermeidet, dass
# HyperVService._session() bei jeder Verbindung die krb5.conf im INI-Format
# neu parsen muesste, nur um den Realm fuer den Principal-String
# ('user@REALM', siehe app.core.kerberos_auth) zu kennen.
REALM_SIDECAR_FILENAME = "realm.txt"


def managed_krb5_conf_path(settings: Settings) -> Path:
    """Fester, von der GUI verwalteter krb5.conf-Pfad."""
    return Path(settings.kerberos_config_dir) / MANAGED_KRB5_CONF_FILENAME


def _realm_sidecar_path(settings: Settings) -> Path:
    return Path(settings.kerberos_config_dir) / REALM_SIDECAR_FILENAME


def resolved_realm(settings: Settings) -> str | None:
    """Aktuell konfiguriertes Realm, frisch von der Platte gelesen (kein
    Neustart bei einer Aenderung noetig) -- None, falls noch nichts
    gespeichert wurde (HVNB_WINRM_TRANSPORT=kerberos waere dann noch
    nicht nutzbar, siehe HyperVService._session)."""
    path = _realm_sidecar_path(settings)
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def ensure_krb5_config_env(settings: Settings) -> None:
    """Zeigt KRB5_CONFIG auf die verwaltete Datei -- einmal beim App-Start
    aufgerufen (app.main), damit libkrb5 (ueber das gssapi-Paket) dort
    liest statt im systemweiten /etc/krb5.conf. Muss VOR jedem
    Ticket-Erwerb bereits gesetzt sein, ist aber unabhaengig davon, ob
    die Datei selbst schon existiert (write_krb5_conf kann spaeter, z.B.
    beim ersten Speichern in der GUI, jederzeit nachziehen)."""
    os.environ["KRB5_CONFIG"] = str(managed_krb5_conf_path(settings))


def write_krb5_conf(settings: Settings, realm: str, kdc_hostname: str) -> Path:
    """Schreibt eine minimale krb5.conf mit genau einem Realm/KDC.
    dns_lookup_realm/dns_lookup_kdc bewusst deaktiviert -- der KDC ist
    bereits explizit bekannt (Auto-Erkennung oder manuelle Eingabe, siehe
    app.api.routes.kerberos_config), keine Abhaengigkeit von DNS-SRV-
    Records noetig (vermeidet eine weitere Fehlerquelle, falls diese in
    der Kundenumgebung nicht gepflegt sind)."""
    path = managed_krb5_conf_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    realm_upper = realm.strip().upper()
    kdc = kdc_hostname.strip()
    content = (
        "[libdefaults]\n"
        f"    default_realm = {realm_upper}\n"
        "    dns_lookup_realm = false\n"
        "    dns_lookup_kdc = false\n"
        "    rdns = false\n"
        "    forwardable = true\n"
        "\n"
        "[realms]\n"
        f"    {realm_upper} = {{\n"
        f"        kdc = {kdc}\n"
        f"        admin_server = {kdc}\n"
        "    }\n"
        "\n"
        "[domain_realm]\n"
        f"    .{realm_upper.lower()} = {realm_upper}\n"
        f"    {realm_upper.lower()} = {realm_upper}\n"
    )
    path.write_text(content, encoding="utf-8")
    _realm_sidecar_path(settings).write_text(realm_upper, encoding="utf-8")
    return path


def krb5_config_exists(settings: Settings) -> bool:
    return managed_krb5_conf_path(settings).is_file()
