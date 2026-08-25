from typing import Literal

from pydantic import BaseModel

IgroupOsType = Literal["aix", "hpux", "hyper_v", "linux", "netware", "openvms", "solaris", "vmware", "windows", "xen"]
LunOsType = Literal[
    "aix", "hpux", "hyper_v", "linux", "netware", "openvms", "solaris", "solaris_efi",
    "vmware", "windows", "windows_2008", "windows_gpt", "xen",
]


class IgroupCreate(BaseModel):
    svm_name: str
    name: str
    os_type: IgroupOsType
    protocol: Literal["fcp", "iscsi", "mixed"] = "mixed"
    initiators: list[str] = []


class VolumeCreate(BaseModel):
    svm_name: str
    name: str
    aggregate_name: str
    size_bytes: int
    security_style: Literal["unix", "ntfs", "mixed"] | None = None
    guarantee_type: Literal["volume", "none"] | None = None
    volume_type: Literal["rw", "dp"] | None = None


class VolumeUpdate(BaseModel):
    size_bytes: int | None = None
    state: Literal["online", "offline"] | None = None


class LunCreate(BaseModel):
    svm_name: str
    lun_name: str
    os_type: LunOsType
    size_bytes: int
    volume_name: str
    space_allocation_enabled: bool = False


class LunUpdate(BaseModel):
    size_bytes: int | None = None
    enabled: bool | None = None


class LunMapCreate(BaseModel):
    svm_name: str
    lun_name: str
    igroup_name: str


class SnapMirrorPolicyRuleWrite(BaseModel):
    label: str
    count: int


class SnapmirrorPolicyCreate(BaseModel):
    # ONTAP verlangt bei POST /api/snapmirror/policies immer eine SVM
    # (gegen echte Hardware verifiziert: ohne 'svm' -> 400 "svm.uuid is a
    # required field") -- eine echte clusterweite Policy ohne SVM-Bezug laesst
    # sich ueber REST nicht anlegen, obwohl GET bei manchen Policies scope=cluster
    # zeigt (das sind von ONTAP mitgelieferte Default-Policies).
    svm_name: str
    name: str
    vault_type: Literal["vault", "mirror_vault"] = "vault"
    rules: list[SnapMirrorPolicyRuleWrite]


class SnapmirrorPolicyUpdate(BaseModel):
    rules: list[SnapMirrorPolicyRuleWrite]


class ScheduleCreate(BaseModel):
    name: str
    svm_name: str | None = None
    minutes: list[int]
    hours: list[int] = []
    days: list[int] = []
    weekdays: list[int] = []


class SnapmirrorRelationshipUpdate(BaseModel):
    policy_name: str | None = None
    schedule_name: str | None = None


class SnapmirrorRelationshipCreate(BaseModel):
    source_cluster_id: str
    source_svm_name: str
    source_volume_name: str
    destination_svm_name: str
    destination_volume_name: str
    policy_name: str
    schedule_name: str | None = None


class ClusterPeerCreate(BaseModel):
    peer_cluster_id: str


class SvmPeerCreate(BaseModel):
    local_svm_name: str
    peer_cluster_id: str
    peer_svm_name: str
    applications: list[str] = ["snapmirror"]
