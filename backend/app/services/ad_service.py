"""Active-Directory-Integration fuer die Anmeldung lokaler GUI-Benutzer
und die Verzeichnis-Suche beim proaktiven Hinzufuegen eines AD-Benutzers
(Settings > Benutzer & Rollen).

Bindet sich per LDAP(S) gegen den konfigurierten Domain Controller,
prueft die Anmeldedaten und liest Gruppenmitgliedschaften aus, die
spaeter auf RBAC-Rollen gemappt werden koennen (Gruppe -> Rolle, noch
nicht umgesetzt -- Backlog #12).

Konfiguration kommt GUI-verwaltet aus app.models.ad_config.AdConfig
(nicht mehr aus Settings/.env) -- diese Klasse nimmt die aufgeloesten
Werte direkt als Konstruktor-Argumente entgegen, statt selbst
Settings/DB zu kennen."""

from dataclasses import dataclass, field

from ldap3 import ALL, NTLM, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

SEARCH_RESULT_LIMIT = 25


@dataclass
class ADAuthResult:
    success: bool
    display_name: str = ""
    email: str = ""
    groups: list[str] | None = None
    error: str | None = None


@dataclass
class ADUserSummary:
    username: str
    display_name: str = ""
    email: str = ""


@dataclass
class ADSearchResult:
    success: bool
    users: list[ADUserSummary] = field(default_factory=list)
    error: str | None = None


class ActiveDirectoryService:
    def __init__(self, server: str, domain: str, base_dn: str, use_ssl: bool = True):
        self._server = server
        self._domain = domain
        self._base_dn = base_dn
        self._use_ssl = use_ssl

    def authenticate(self, username: str, password: str) -> ADAuthResult:
        server = Server(self._server, use_ssl=self._use_ssl, get_info=ALL)
        user_principal = f"{self._domain}\\{username}"

        try:
            conn = Connection(server, user=user_principal, password=password, authentication=NTLM)
            if not conn.bind():
                return ADAuthResult(success=False, error="Ungueltige Anmeldedaten")

            conn.search(
                search_base=self._base_dn,
                search_filter=f"(sAMAccountName={escape_filter_chars(username)})",
                attributes=["displayName", "mail", "memberOf"],
            )

            if not conn.entries:
                return ADAuthResult(success=True, display_name=username, groups=[])

            entry = conn.entries[0]
            groups = [str(g) for g in entry.memberOf] if "memberOf" in entry else []
            return ADAuthResult(
                success=True,
                display_name=str(entry.displayName) if "displayName" in entry else username,
                email=str(entry.mail) if "mail" in entry else "",
                groups=groups,
            )
        except LDAPException as exc:
            return ADAuthResult(success=False, error=str(exc))

    def search_users(self, bind_user: str, bind_password: str, query: str) -> ADSearchResult:
        """Durchsucht das Verzeichnis nach Benutzern (sAMAccountName ODER
        displayName enthaelt `query`) -- bindet dafuer mit einem
        dedizierten Lese-Service-Konto, NICHT mit den Zugangsdaten des
        suchenden Admins (siehe AdConfig.bind_user/-password). Begrenzt
        auf SEARCH_RESULT_LIMIT Treffer, um ein versehentliches
        Komplett-Auflisten des Verzeichnisses zu vermeiden."""
        if not bind_user or not bind_password:
            return ADSearchResult(success=False, error="AD-Suche ist nicht konfiguriert (kein Lese-Service-Konto hinterlegt)")
        if not query.strip():
            return ADSearchResult(success=False, error="Suchbegriff darf nicht leer sein")

        server = Server(self._server, use_ssl=self._use_ssl, get_info=ALL)
        bind_principal = f"{self._domain}\\{bind_user}"
        escaped = escape_filter_chars(query.strip())

        try:
            conn = Connection(server, user=bind_principal, password=bind_password, authentication=NTLM)
            if not conn.bind():
                return ADSearchResult(success=False, error="Service-Konto konnte sich nicht anmelden (Zugangsdaten pruefen)")

            conn.search(
                search_base=self._base_dn,
                search_filter=(
                    "(&(objectClass=user)(objectCategory=person)"
                    f"(|(sAMAccountName=*{escaped}*)(displayName=*{escaped}*)))"
                ),
                attributes=["sAMAccountName", "displayName", "mail"],
                size_limit=SEARCH_RESULT_LIMIT,
            )

            users = [
                ADUserSummary(
                    username=str(entry.sAMAccountName) if "sAMAccountName" in entry else "",
                    display_name=str(entry.displayName) if "displayName" in entry else "",
                    email=str(entry.mail) if "mail" in entry else "",
                )
                for entry in conn.entries
                if "sAMAccountName" in entry
            ]
            return ADSearchResult(success=True, users=users)
        except LDAPException as exc:
            return ADSearchResult(success=False, error=str(exc))
