from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = False
    server: str = ""
    domain: str = ""
    base_dn: str = ""
    use_ssl: bool = True
    bind_user: str = ""
    # Nie das echte Passwort zurueckgeben -- nur ob eines hinterlegt ist
    # (fuer die "gesetzt"/"nicht gesetzt"-Anzeige im Formular).
    bind_password_set: bool = False
    updated_at: datetime | None = None
    updated_by: str | None = None


class AdConfigWrite(BaseModel):
    enabled: bool = False
    server: str = ""
    domain: str = ""
    base_dn: str = ""
    use_ssl: bool = True
    bind_user: str = ""
    # Leer lassen behaelt das bereits gespeicherte Passwort (gleiche
    # Konvention wie bei RestoreProxyHost/HyperVCluster).
    bind_password: str | None = None


class AdTestRequest(BaseModel):
    """Testet gegen die im Formular EINGEGEBENEN (noch nicht zwingend
    gespeicherten) Werte -- bindet mit dem angegebenen Service-Konto,
    ohne etwas zu speichern. Ein leeres bind_password nutzt (falls
    vorhanden) das bereits gespeicherte Passwort, analog zu Write."""

    server: str
    domain: str
    base_dn: str
    use_ssl: bool = True
    bind_user: str
    bind_password: str | None = None


class AdTestResult(BaseModel):
    success: bool
    message: str
