from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EmailConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_encryption: str
    smtp_username: str | None = None
    has_password: bool = False
    from_address: str
    from_name: str
    recipients: str
    notify_on_restore_failure: bool
    daily_summary_enabled: bool
    daily_summary_hour: int
    last_test_at: datetime | None = None
    last_test_error: str | None = None
    updated_at: datetime | None = None


class EmailConfigUpdate(BaseModel):
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_encryption: str
    smtp_username: str | None = None
    # Leer/None gelassen = bestehendes Kennwort behalten (analog zum Muster
    # bei anderen Zugangsdaten-Formularen), nur bei Angabe wird verschluesselt
    # neu gespeichert.
    smtp_password: str | None = None
    from_address: str
    from_name: str
    recipients: str
    notify_on_restore_failure: bool
    daily_summary_enabled: bool
    daily_summary_hour: int


class EmailTestRequest(BaseModel):
    recipient: str
