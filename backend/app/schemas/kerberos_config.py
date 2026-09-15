from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KerberosConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    realm: str | None = None
    kdc_hostname: str | None = None
    kdc_address: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class KerberosConfigWrite(BaseModel):
    realm: str
    kdc_hostname: str
    kdc_address: str | None = None


class KerberosDetectRequest(BaseModel):
    cluster_id: str


class KerberosDetectResult(BaseModel):
    """Vorschlag aus der Live-Abfrage gegen einen bereits registrierten
    Cluster (per dessen aktuell funktionierendem Transport) -- wird dem
    Nutzer zur Bestaetigung angezeigt, nicht automatisch gespeichert."""

    realm: str
    kdc_hostname: str
    kdc_address: str | None = None


class KerberosTestRequest(BaseModel):
    """Testet gegen die im Formular EINGEGEBENEN (noch nicht zwingend
    gespeicherten) Werte -- 'Testen' und 'Speichern' schreiben dieselbe
    krb5.conf (app.core.kerberos_config.write_krb5_conf), nur 'Speichern'
    persistiert zusaetzlich die KerberosConfig-Zeile fuer die GUI-Anzeige."""

    cluster_id: str
    realm: str
    kdc_hostname: str


class KerberosTestResult(BaseModel):
    success: bool
    message: str
