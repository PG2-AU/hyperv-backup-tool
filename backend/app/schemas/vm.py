from pydantic import BaseModel


class VhdInfo(BaseModel):
    name: str
    size_bytes: int
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
    csv_paths: list[str] = []
    vhdx_size_bytes: int | None = None
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
