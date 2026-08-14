"""Active-Directory-Integration fuer die Anmeldung lokaler GUI-Benutzer.

Bindet sich per LDAP(S) gegen den konfigurierten Domain Controller,
prueft die Anmeldedaten und liest Gruppenmitgliedschaften aus, die
spaeter auf RBAC-Rollen gemappt werden koennen (Gruppe -> Rolle).
"""

from dataclasses import dataclass

from ldap3 import ALL, NTLM, Connection, Server
from ldap3.core.exceptions import LDAPException

from app.core.config import Settings


@dataclass
class ADAuthResult:
    success: bool
    display_name: str = ""
    email: str = ""
    groups: list[str] | None = None
    error: str | None = None


class ActiveDirectoryService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def authenticate(self, username: str, password: str) -> ADAuthResult:
        if not self._settings.ad_enabled:
            return ADAuthResult(success=False, error="AD-Integration ist deaktiviert")

        server = Server(self._settings.ad_server, use_ssl=self._settings.ad_use_ssl, get_info=ALL)
        user_principal = f"{self._settings.ad_domain}\\{username}"

        try:
            conn = Connection(server, user=user_principal, password=password, authentication=NTLM)
            if not conn.bind():
                return ADAuthResult(success=False, error="Ungueltige Anmeldedaten")

            conn.search(
                search_base=self._settings.ad_base_dn,
                search_filter=f"(sAMAccountName={username})",
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
