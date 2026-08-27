from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration, ueber ENV-Variablen oder .env steuerbar."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="HVNB_", extra="ignore")

    app_name: str = "Hyper-V NetApp Backup"
    environment: Literal["development", "production"] = "development"

    # --- Security / Auth ---
    secret_key: str = Field(default="CHANGE_ME_IN_PRODUCTION")
    access_token_expire_minutes: int = 60 * 8
    algorithm: str = "HS256"

    # --- Datenbank ---
    database_url: str = "sqlite:///./data/app.db"

    # --- Active Directory ---
    ad_enabled: bool = False
    ad_server: str = ""
    ad_domain: str = ""
    ad_base_dn: str = ""
    ad_bind_user: str = ""
    ad_bind_password: str = ""
    ad_use_ssl: bool = True

    # --- NetApp ONTAP ---
    ontap_cluster_mgmt_lif: str = ""
    ontap_username: str = ""
    ontap_password: str = ""
    ontap_verify_ssl: bool = True
    ontap_is_metrocluster: bool = False
    netapp_cert_dir: str = "/data/netapp-certs"

    # --- Hyper-V / WinRM ---
    winrm_transport: Literal["ntlm", "kerberos", "credssp"] = "ntlm"
    winrm_use_https: bool = True
    winrm_port: int = 5986

    # --- Periodischer Hintergrundabgleich (app.core.scheduler) ---
    healthcheck_interval_minutes: int = 15
    discovery_interval_minutes: int = 240
    snapshot_reconcile_hour: int = 2  # 0-23, taeglich zu dieser Stunde (UTC)

    # --- HTTPS / GUI ---
    tls_cert_path: str = "/etc/hvnb/certs/server.crt"
    tls_key_path: str = "/etc/hvnb/certs/server.key"

    # --- Git-basiertes Deployment ---
    git_repo_url: str = ""
    git_branch: str = "main"
    auto_update_enabled: bool = False
    auto_update_interval_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
