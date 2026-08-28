from pydantic import BaseModel


class VersionInfo(BaseModel):
    """Aktuell deployter Git-Commit + Zeitpunkt des letzten Deploys, fuer die
    Fusszeile im Hauptmenue -- fuer alle angemeldeten Benutzer sichtbar
    (keine sensiblen Daten), daher separat von PublicSettings/SETTINGS_MANAGE."""

    commit: str | None = None
    commit_short: str | None = None
    commit_count: int | None = None
    last_deploy_at: str | None = None
    last_health_check_at: str | None = None
    last_discovery_at: str | None = None
    last_snapshot_reconciliation_at: str | None = None
    last_retention_cleanup_at: str | None = None


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
