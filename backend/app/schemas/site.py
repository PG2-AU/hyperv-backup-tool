from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SiteBadge(BaseModel):
    """Kompakte Standort-Angabe fuer Inventory-Badges (VmRead/CsvRead)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    color: str


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    color: str
    created_at: datetime


class SiteWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    color: str = Field(default="blue", min_length=1, max_length=30)


class NodeAssignmentRead(BaseModel):
    node_name: str
    site_id: str | None = None
    vm_count: int = 0


class HyperVClusterAssignmentRead(BaseModel):
    cluster_id: str
    cluster_name: str
    nodes: list[NodeAssignmentRead]


class NetAppClusterAssignmentRead(BaseModel):
    netapp_cluster_id: str
    name: str
    is_metrocluster: bool
    metrocluster_mode: str | None = None
    site_id: str | None = None


class CsvAssignmentRead(BaseModel):
    cluster_id: str
    cluster_name: str
    csv_name: str
    disk_serial_number: str | None = None
    netapp_cluster_name: str | None = None
    # Vom NetApp-System geerbter Standort (ohne Override).
    inherited_site_id: str | None = None
    override_site_id: str | None = None


class SiteAssignmentsRead(BaseModel):
    hyperv_clusters: list[HyperVClusterAssignmentRead]
    netapp_clusters: list[NetAppClusterAssignmentRead]
    csvs: list[CsvAssignmentRead]
    # NetApp-Systeme, die gerade nicht im MetroCluster-Normalbetrieb laufen
    # -- solange nicht leer, ist die Abweichungs-Pruefung ausgesetzt.
    switchover_clusters: list[str] = []


class NodeAssignmentWrite(BaseModel):
    cluster_id: str
    node_name: str = Field(min_length=1, max_length=255)
    site_id: str | None = None


class NetAppAssignmentWrite(BaseModel):
    netapp_cluster_id: str
    site_id: str | None = None


class CsvOverrideWrite(BaseModel):
    cluster_id: str
    disk_serial_number: str = Field(min_length=1, max_length=100)
    csv_name: str | None = None
    site_id: str | None = None


class SiteMismatchSummary(BaseModel):
    """Dashboard-Kachel: Anzahl VMs mit Standort-Abweichung."""

    sites_configured: bool
    mismatch_count: int
    # VMs, deren Host ODER mindestens eine Disk noch keinem Standort
    # zugeordnet ist -- Hinweis, dass die Kennzeichnung unvollstaendig ist.
    unassigned_count: int
    switchover_clusters: list[str] = []
