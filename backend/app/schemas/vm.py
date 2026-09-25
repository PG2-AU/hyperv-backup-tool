from pydantic import BaseModel

from app.schemas.site import SiteBadge


class VhdInfo(BaseModel):
    name: str
    size_bytes: int
    # Tatsaechlich belegter Platz der VHDX-Datei auf dem darunterliegenden
    # CSV (Get-VHD -> FileSize) -- bei einer dynamisch wachsenden VHDX i.d.R.
    # kleiner als size_bytes (logische/maximale Groesse, Get-VHD -> Size).
    # Basis fuer die Kapazitaetsabschaetzung beim Restore (siehe
    # RestoreWizardModal.tsx), da beim Kopieren der Datei genau dieser Wert
    # an Platz auf dem Ziel-CSV belegt wird, nicht die logische Groesse.
    used_bytes: int | None = None
    csv_path: str
    full_path: str


class NetworkAdapterRead(BaseModel):
    name: str
    mac_address: str | None = None
    switch_name: str | None = None
    vlan_id: int | None = None


class CheckpointRead(BaseModel):
    name: str
    id: str
    creation_time: str
    # 'hvnb_'-Praefix = von dieser App selbst erstellt (siehe
    # _execute_job_run/create_checkpoint), typischerweise ein Ueberbleibsel
    # eines abgebrochenen Backup-Laufs. Alles andere: vermutlich manuell in
    # Hyper-V Manager erstellt.
    app_created: bool = False


class VmRead(BaseModel):
    id: str
    name: str
    state: str
    host: str
    cluster: str | None = None
    # Stabile HyperVCluster.id (anders als `cluster`, der Anzeigename) --
    # noetig, um eine VM cluster-eindeutig zu identifizieren, wenn zwei
    # Cluster eine VM mit demselben Namen haben (siehe app.models.resource_group).
    cluster_id: str | None = None
    csv_paths: list[str] = []
    # UNC-Pfade ('\\server\share') der NetApp-CIFS-Freigaben, auf denen VHDs
    # dieser VM liegen (Backlog #22) -- parallel zu csv_paths, eine VHD hat
    # nie beides gleichzeitig gesetzt.
    smb_share_paths: list[str] = []
    vhdx_size_bytes: int | None = None
    vhdx_used_bytes: int | None = None
    vhds: list[VhdInfo] = []
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    policy_ids: list[str] = []
    protected: bool = False
    cpu_count: int | None = None
    generation: int | None = None
    memory_startup_bytes: int | None = None
    memory_minimum_bytes: int | None = None
    memory_maximum_bytes: int | None = None
    dynamic_memory_enabled: bool | None = None
    network_adapters: list[NetworkAdapterRead] = []
    pci_devices: list[str] = []
    checkpoints: list[CheckpointRead] = []
    # Standort-Kennzeichnung (siehe app.core.sites): Standort des Hosts,
    # eindeutige Standorte der Disks, und ob beide voneinander abweichen.
    host_site: SiteBadge | None = None
    storage_sites: list[SiteBadge] = []
    site_mismatch: bool = False
    # CSVs/Freigaben, deren Standort vom Host-Standort abweicht.
    site_mismatch_storage: list[str] = []
    # Host oder mindestens eine Disk noch keinem Standort zugeordnet.
    site_unassigned: bool = False


class CsvRead(BaseModel):
    name: str
    owner_node: str
    state: str
    hyperv_cluster_name: str | None = None
    # Stabile HyperVCluster.id (anders als hyperv_cluster_name, der
    # Anzeigename) -- siehe VmRead.cluster_id.
    cluster_id: str | None = None
    volume_path: str
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    lun_name: str | None = None
    lun_capacity_bytes: int | None = None
    lun_used_bytes: int | None = None
    volume_name: str | None = None
    volume_capacity_bytes: int | None = None
    volume_used_bytes: int | None = None
    svm_name: str | None = None
    netapp_cluster_name: str | None = None
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    policy_ids: list[str] = []
    protected: bool = False
    # Effektiver Standort (siehe app.core.sites.SiteResolver.csv_site) und
    # dessen Quelle: 'netapp' (geerbt) oder 'override' (manuell an der CSV).
    site: SiteBadge | None = None
    site_source: str | None = None


class SmbShareRead(BaseModel):
    """SMB3/CIFS-Freigabe, auf der Hyper-V-VMs direkt liegen (Backlog #22) --
    Pendant zu CsvRead, aber ohne LUN-Konzept (der NetApp-Volume-Bezug ist
    direkt, kein Seriennummer-Umweg ueber eine Block-LUN)."""

    server: str
    share: str
    hyperv_cluster_name: str | None = None
    cluster_id: str | None = None
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    volume_name: str | None = None
    svm_name: str | None = None
    netapp_cluster_name: str | None = None
    resource_group_names: list[str] = []
    policy_names: list[str] = []
    policy_ids: list[str] = []
    protected: bool = False
