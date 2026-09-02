from pydantic import BaseModel


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
