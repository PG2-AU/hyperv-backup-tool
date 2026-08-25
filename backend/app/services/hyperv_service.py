"""Hyper-V Service: Steuerung von Windows Server 2022 Hosts/Clustern per
PowerShell-Remoting (WinRM) aus dem Linux-Container heraus.

Unterstuetzt Backups im VM-Scope sowie CSV/Cluster-Scope. Fuer
applikationskonsistente Sicherungen werden Hyper-V "Production
Checkpoints" (VSS-basiert) verwendet, fuer crash-konsistente Sicherungen
"Standard Checkpoints" (Saved-State-basiert).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

import winrm

from app.core.config import Settings


class ConsistencyType(StrEnum):
    APPLICATION_CONSISTENT = "ApplicationConsistent"  # Hyper-V Production Checkpoint (VSS)
    CRASH_CONSISTENT = "CrashConsistent"  # Hyper-V Standard Checkpoint


@dataclass
class VirtualMachineInfo:
    name: str
    id: str
    state: str
    host: str
    csv_paths: list[str] = field(default_factory=list)
    vhdx_size_bytes: int = 0


@dataclass
class ClusterSharedVolumeInfo:
    name: str
    owner_node: str
    state: str
    volume_path: str
    capacity_bytes: int = 0
    used_bytes: int = 0


@dataclass
class CheckpointInfo:
    vm_name: str
    checkpoint_name: str
    checkpoint_id: str
    created_at: str


@dataclass
class CommandResult:
    success: bool
    output: str = ""
    error: str = ""


@dataclass
class HyperVClusterNodeSummary:
    name: str
    state: str


@dataclass
class HyperVClusterSummary:
    cluster_name: str
    node_count: int
    healthy_node_count: int
    nodes: list[HyperVClusterNodeSummary] = field(default_factory=list)


class HyperVConnectionError(Exception):
    """Verbindungsaufbau oder Cluster-Abfrage per WinRM fehlgeschlagen."""


class HyperVService:
    def __init__(self, settings: Settings, target_host: str):
        self._settings = settings
        self._target_host = target_host

    def _session(self, username: str, password: str) -> winrm.Session:
        scheme = "https" if self._settings.winrm_use_https else "http"
        endpoint = f"{scheme}://{self._target_host}:{self._settings.winrm_port}/wsman"
        return winrm.Session(
            endpoint,
            auth=(username, password),
            transport=self._settings.winrm_transport,
            server_cert_validation="validate" if self._settings.winrm_use_https else "ignore",
        )

    def _run_ps(self, session: winrm.Session, script: str) -> CommandResult:
        result = session.run_ps(script)
        return CommandResult(
            success=result.status_code == 0,
            output=result.std_out.decode("utf-8", errors="replace"),
            error=result.std_err.decode("utf-8", errors="replace"),
        )

    def get_cluster_summary(self, username: str, password: str) -> HyperVClusterSummary:
        """Verbindungstest + Basisinfo (Cluster-Name, Knoten-Status). Wird
        sowohl beim Hinzufuegen eines Hyper-V-Clusters als auch fuer die
        regelmaessige Aktualisierung der Cluster-Uebersicht genutzt.
        Verbindet zum Cluster Name Object (CNO) -- WinRM/PS-Remoting dorthin
        routet transparent zum jeweils aktiven Knoten."""
        script = (
            "$cluster = Get-Cluster; "
            "$nodes = Get-ClusterNode | Select-Object Name, State; "
            "[PSCustomObject]@{ ClusterName = $cluster.Name; Nodes = $nodes } | ConvertTo-Json -Depth 4"
        )
        try:
            session = self._session(username, password)
            result = self._run_ps(session, script)
        except Exception as exc:  # WinRM-Transportfehler (Timeout, DNS, TLS, Auth) sind keine einheitliche Exception-Klasse
            raise HyperVConnectionError(str(exc)) from exc
        if not result.success:
            raise HyperVConnectionError(result.error or "Get-Cluster/Get-ClusterNode fehlgeschlagen")

        try:
            data = json.loads(result.output)
        except json.JSONDecodeError as exc:
            raise HyperVConnectionError(f"Unerwartete Antwort: {result.output}") from exc

        nodes_raw = data.get("Nodes") or []
        nodes_raw = nodes_raw if isinstance(nodes_raw, list) else [nodes_raw]
        nodes = [HyperVClusterNodeSummary(name=n["Name"], state=str(n["State"])) for n in nodes_raw]
        healthy_count = sum(1 for n in nodes if n.state == "Up")

        return HyperVClusterSummary(
            cluster_name=data.get("ClusterName") or self._target_host,
            node_count=len(nodes),
            healthy_node_count=healthy_count,
            nodes=nodes,
        )

    def list_vms(self, session: winrm.Session) -> list[VirtualMachineInfo]:
        script = (
            "Get-VM | Select-Object Name, Id, State, ComputerName, "
            "@{N='CsvPaths';E={($_.HardDrives).Path}}, "
            "@{N='VhdSizeBytes';E={"
            "($_.HardDrives | ForEach-Object { (Get-VHD -Path $_.Path).Size } | Measure-Object -Sum).Sum"
            "}} | ConvertTo-Json -Depth 4"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Get-VM fehlgeschlagen: {result.error}")

        raw = json.loads(result.output or "[]")
        entries = raw if isinstance(raw, list) else [raw]
        return [
            VirtualMachineInfo(
                name=e["Name"],
                id=e["Id"],
                state=str(e["State"]),
                host=e.get("ComputerName", self._target_host),
                csv_paths=[p for p in (e.get("CsvPaths") or []) if p],
                vhdx_size_bytes=int(e.get("VhdSizeBytes") or 0),
            )
            for e in entries
        ]

    def list_csvs(self, session: winrm.Session) -> list[ClusterSharedVolumeInfo]:
        script = (
            "Get-ClusterSharedVolume | Select-Object Name, State, "
            "@{N='OwnerNode';E={$_.OwnerNode.Name}}, "
            "@{N='VolumePath';E={$_.SharedVolumeInfo.FriendlyVolumeName}}, "
            "@{N='CapacityBytes';E={$_.SharedVolumeInfo.Partition.Size}}, "
            "@{N='FreeBytes';E={$_.SharedVolumeInfo.Partition.FreeSpace}} "
            "| ConvertTo-Json -Depth 4"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Get-ClusterSharedVolume fehlgeschlagen: {result.error}")
        raw = json.loads(result.output or "[]")
        entries = raw if isinstance(raw, list) else [raw]
        return [
            ClusterSharedVolumeInfo(
                name=e["Name"],
                owner_node=str(e.get("OwnerNode", "")),
                state=str(e["State"]),
                volume_path=e.get("VolumePath") or "",
                capacity_bytes=int(e.get("CapacityBytes") or 0),
                used_bytes=int(e.get("CapacityBytes") or 0) - int(e.get("FreeBytes") or 0),
            )
            for e in entries
        ]

    def create_checkpoint(
        self,
        session: winrm.Session,
        vm_name: str,
        checkpoint_name: str,
        consistency: ConsistencyType,
    ) -> CheckpointInfo:
        # Hyper-V waehlt Production- vs. Standard-Checkpoint ueber die
        # VM-Einstellung `CheckpointType`; vor dem Checkpoint kurz umschalten,
        # damit ein einzelner Host gemischte Konsistenz-Level pro Job faehrt.
        script = f"""
        $vm = Get-VM -Name '{vm_name}'
        $originalType = $vm.CheckpointType
        Set-VM -Name '{vm_name}' -CheckpointType {"Production" if consistency == ConsistencyType.APPLICATION_CONSISTENT else "Standard"}
        try {{
            $cp = Checkpoint-VM -Name '{vm_name}' -SnapshotName '{checkpoint_name}' -Passthru
            $cp | Select-Object VMName, Name, Id, CreationTime | ConvertTo-Json
        }} finally {{
            Set-VM -Name '{vm_name}' -CheckpointType $originalType
        }}
        """
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Checkpoint fuer '{vm_name}' fehlgeschlagen: {result.error}")

        data = json.loads(result.output)
        return CheckpointInfo(
            vm_name=data["VMName"],
            checkpoint_name=data["Name"],
            checkpoint_id=data["Id"],
            created_at=str(data["CreationTime"]),
        )

    def remove_checkpoint(self, session: winrm.Session, vm_name: str, checkpoint_name: str) -> CommandResult:
        """Wird sowohl im Normalbetrieb (nach erfolgreichem SnapMirror-Transfer)
        als auch im Fehlerfall zum automatischen Aufraeumen verwendet."""
        script = f"Remove-VMSnapshot -VMName '{vm_name}' -Name '{checkpoint_name}' -Confirm:$false"
        return self._run_ps(session, script)

    def cleanup_checkpoints(self, session: winrm.Session, checkpoints: list[tuple[str, str]]) -> list[CommandResult]:
        """Best-effort Rollback ueber mehrere VM-Checkpoints (vm_name, checkpoint_name)."""
        return [self.remove_checkpoint(session, vm_name, cp_name) for vm_name, cp_name in checkpoints]
