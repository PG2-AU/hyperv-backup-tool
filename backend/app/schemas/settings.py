from pydantic import BaseModel


class PublicSettings(BaseModel):
    """Nicht-sensitive Teilmenge der Server-Konfiguration fuer die GUI."""

    environment: str

    ad_enabled: bool
    ad_server: str
    ad_domain: str
    ad_base_dn: str

    ontap_cluster_mgmt_lif: str
    ontap_verify_ssl: bool
    ontap_is_metrocluster: bool

    winrm_transport: str
    winrm_use_https: bool
    winrm_port: int

    git_repo_url: str
    git_branch: str
    auto_update_enabled: bool
    auto_update_interval_minutes: int
