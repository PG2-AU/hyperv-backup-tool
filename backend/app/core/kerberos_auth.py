"""Passwortbasierte Kerberos-Ticket-Beschaffung zur Laufzeit fuer
HVNB_WINRM_TRANSPORT=kerberos (Backlog-Punkt 50).

Kein Domain-Join, kein Keytab: mit den bereits verschluesselt
gespeicherten Zugangsdaten (dieselben, die heute schon fuer NTLM/CredSSP
verwendet werden) wird bei Bedarf direkt beim KDC ein Ticket angefordert
(AS-REQ/AS-REP ueber das `gssapi`-Paket), analog zu `kinit`. Funktioniert
von jedem Rechner aus, der den KDC per Netzwerk erreicht -- kein
Computer-Konto/Domain-Join noetig.

**Nebenlaeufigkeit -- warum eine DIR:-Ccache-Sammlung statt eines
MEMORY:-Ccache je Thread**: die Backup-Checkpoint-Erstellung laeuft
parallel (ein Thread je Hyper-V-Knoten, siehe _run_node_checkpoints in
jobs.py), und verschiedene Hyper-V-Cluster koennen unterschiedliche
Zugangsdaten haben. `KRB5CCNAME` ist eine PROZESSWEITE Umgebungsvariable
-- sie bei jedem Ticket-Erwerb auf einen anderen Wert zu setzen waere bei
echter Parallelitaet racy (ein Thread koennte mitten in seiner WinRM-
Verbindung stehen, waehrend ein anderer Thread KRB5CCNAME auf einen
anderen Nutzer umbiegt). Stattdessen: EIN fester `DIR:`-Ccache-Sammlungs-
Pfad, EINMALIG bei App-Start gesetzt (siehe ensure_ccache_env, aufgerufen
aus app.main) und nie wieder veraendert -- eine DIR:-Sammlung haelt
Tickets MEHRERER Prinzipale gleichzeitig. `pywinrm` uebergibt beim
Verbinden bereits von sich aus `principal=self.username` an
`HTTPKerberosAuth` (siehe winrm/transport.py, unveraendert) -- damit
waehlt es beim eigentlichen WinRM-Request automatisch das richtige
Ticket aus der Sammlung, ganz ohne Umgebungsvariable-Jonglieren pro
Aufruf. Nur die BESCHAFFUNG (Schreiben in die Sammlung) wird per Lock
serialisiert, nicht das Lesen/Verwenden -- das ist mit einer DIR:-
Sammlung fuer mehrere gleichzeitige Nutzer sicher.

**Principal-Format**: die App speichert Zugangsdaten oft im Windows-
Format 'DOMAIN\\user' (NTLM/CredSSP). Kerberos-Prinzipale sind dagegen
'user@REALM' (UPN-aehnlich). `_principal_for` normalisiert das an EINER
Stelle, damit Ticket-Beschaffung (hier) und die Uebergabe an
`winrm.Session`/`HTTPKerberosAuth` (siehe HyperVService._session) exakt
denselben String verwenden -- sonst findet pywinrm das gerade erst
beschaffte Ticket in der Sammlung nicht wieder.
"""

import logging
import os
import threading
import time
from pathlib import Path

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Vor Ablauf neu beschaffen, nicht erst wenn das Ticket schon ungueltig
# ist -- vermeidet, dass eine gerade laufende WinRM-Operation mitten im
# Ablauf ein Ticket verliert.
TICKET_REFRESH_MARGIN_SECONDS = 30 * 60
# Fallback, falls das beschaffte Ticket keine auslesbare Ablaufzeit traegt
# (sollte praktisch nicht vorkommen) -- konservativ kurz gewaehlt, lieber
# einmal zu frueh neu beschaffen als ein abgelaufenes Ticket zu verwenden.
DEFAULT_TICKET_LIFETIME_FALLBACK_SECONDS = 4 * 60 * 60

_lock = threading.Lock()
_cached_until: dict[str, float] = {}


class KerberosTicketError(RuntimeError):
    """Ticket-Beschaffung fehlgeschlagen -- klare, im Schritt-Protokoll
    anzeigbare Meldung statt einer rohen GSSAPI-Exception."""


def _principal_for(username: str, realm: str) -> str:
    """'DOMAIN\\user' oder blankes 'user' -> 'user@REALM'. Ein bereits
    UPN-foermiger Wert ('user@REALM') wird unveraendert durchgereicht."""
    if "@" in username:
        return username
    local_part = username.split("\\", 1)[-1]
    return f"{local_part}@{realm.strip().upper()}"


def ensure_ccache_env(settings: Settings) -> None:
    """Siehe Modul-Docstring -- einmalig bei App-Start aufrufen (app.main),
    unabhaengig davon, ob HVNB_WINRM_TRANSPORT ueberhaupt kerberos ist
    (schadlos, falls nicht genutzt)."""
    ccache_dir = Path(settings.kerberos_config_dir) / "ccaches"
    ccache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KRB5CCNAME"] = f"DIR:{ccache_dir}"


def ensure_ticket(username: str, password: str, realm: str) -> str:
    """Stellt sicher, dass fuer diesen Nutzer ein noch gueltiges Ticket in
    der gemeinsamen Ccache-Sammlung liegt (beschafft bei Bedarf ein neues),
    und gibt den zu verwendenden Principal-String zurueck (siehe
    _principal_for -- HyperVService._session braucht ihn fuer
    HTTPKerberosAuth's principal-Parameter).

    Schneller Pfad ohne Lock, wenn bereits aktuell (haeufigster Fall --
    Tickets halten mehrere Stunden, die meisten Aufrufe treffen also
    nicht auf eine noetige Neubeschaffung). Neubeschaffung selbst per
    Lock serialisiert (ein einzelner globaler Lock reicht -- Beschaffung
    ist selten, die Mehrkosten durch Serialisierung sind vernachlaessigbar
    gegenueber dem Risiko einer doppelten parallelen Beschaffung)."""
    principal = _principal_for(username, realm)
    now = time.time()
    if _cached_until.get(principal, 0) - TICKET_REFRESH_MARGIN_SECONDS > now:
        return principal
    with _lock:
        if _cached_until.get(principal, 0) - TICKET_REFRESH_MARGIN_SECONDS > now:
            return principal  # ein anderer Thread war waehrend des Wartens schon erfolgreich
        # Lazy-Import: gssapi/pykerberos sind nur bei tatsaechlicher
        # Kerberos-Nutzung im Image vorhanden (siehe docker/Dockerfile) --
        # ein Import auf Modulebene wuerde JEDEN Start dieser App von den
        # C-Extensions abhaengig machen, auch wenn nur NTLM/CredSSP genutzt
        # wird.
        try:
            import gssapi
            from gssapi.raw import acquire_cred_with_password, store_cred_into
        except ImportError as exc:
            raise KerberosTicketError(
                "Kerberos-Unterstuetzung (gssapi/pykerberos) ist nicht installiert."
            ) from exc
        try:
            name = gssapi.Name(principal, gssapi.NameType.user)
            raw_cred = acquire_cred_with_password(name, password.encode("utf-8"), usage="initiate")
            ccache_name = os.environ.get("KRB5CCNAME")
            if not ccache_name:
                raise KerberosTicketError("KRB5CCNAME ist nicht gesetzt -- ensure_ccache_env() wurde nicht aufgerufen.")
            store_cred_into({"ccache": ccache_name}, raw_cred.creds, overwrite=True)
        except KerberosTicketError:
            raise
        except Exception as exc:  # gssapi.exceptions.GSSError u.a. -- breit gefangen fuer eine einheitliche Fehlermeldung
            raise KerberosTicketError(f"Kerberos-Ticket fuer '{principal}' konnte nicht beschafft werden: {exc}") from exc
        lifetime = getattr(raw_cred, "lifetime", None) or DEFAULT_TICKET_LIFETIME_FALLBACK_SECONDS
        _cached_until[principal] = now + lifetime
        logger.info("Kerberos-Ticket fuer '%s' beschafft, gueltig ca. %d Minuten.", principal, lifetime // 60)
    return principal
