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


class LunCreate(BaseModel):
    svm_name: str
    lun_name: str
    os_type: LunOsType
    size_bytes: int
    volume_name: str


class LunMapCreate(BaseModel):
    svm_name: str
    lun_name: str
    igroup_name: str


class ClusterPeerCreate(BaseModel):
    peer_cluster_id: str


class SvmPeerCreate(BaseModel):
    local_svm_name: str
    peer_cluster_id: str
    peer_svm_name: str
    applications: list[str] = ["snapmirror"]
