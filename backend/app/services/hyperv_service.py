"""Hyper-V Service: Steuerung von Windows Server 2022 Hosts/Clustern per
PowerShell-Remoting (WinRM) aus dem Linux-Container heraus.

Unterstuetzt Backups im VM-Scope sowie CSV/Cluster-Scope. Fuer
applikationskonsistente Sicherungen werden Hyper-V "Production
Checkpoints" (VSS-basiert) verwendet, fuer crash-konsistente Sicherungen
"Standard Checkpoints" (Saved-State-basiert).
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from enum import StrEnum

import winrm

from app.core.config import Settings


class ConsistencyType(StrEnum):
    APPLICATION_CONSISTENT = "ApplicationConsistent"  # Hyper-V Production Checkpoint (VSS)
    CRASH_CONSISTENT = "CrashConsistent"  # Hyper-V Standard Checkpoint


@dataclass
class VhdDetail:
    path: str
    size_bytes: int = 0
    used_bytes: int = 0


@dataclass
class VirtualMachineInfo:
    name: str
    id: str
    state: str
    host: str
    vhds: list[VhdDetail] = field(default_factory=list)


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


@dataclass
class DiscoveryStepResult:
    step: str
    success: bool
    message: str
    count: int | None = None


@dataclass
class HyperVDiscoveryData:
    vms: list[VirtualMachineInfo] = field(default_factory=list)


class HyperVConnectionError(Exception):
    """Verbindungsaufbau oder Cluster-Abfrage per WinRM fehlgeschlagen."""


def check_reachability(host: str, port: int, timeout_sec: float = 5.0) -> None:
    """Schneller TCP-Connect-Test auf den WinRM-Port, bevor der volle
    WinRM/PowerShell-Handshake versucht wird. pywinrm's eigener Timeout
    (Standard 30s) deckt zwar auch haengende Verbindungsversuche ab, aber ein
    Host, der Pakete lautlos verwirft (Firewall-DROP statt REJECT), fuehlt
    sich fuer den Nutzer wie ein Absturz ohne Fehlermeldung an, wenn man
    30 Sekunden auf eine generische Fehlermeldung wartet. Dieser Schritt
    scheitert stattdessen in wenigen Sekunden mit einer klaren Ursache."""
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return
    except OSError as exc:
        raise HyperVConnectionError(f"Host '{host}' ist auf Port {port} nicht erreichbar: {exc}") from exc


class HyperVService:
    def __init__(self, settings: Settings, target_host: str, use_https: bool | None = None):
        """'use_https' ist ein Pro-Cluster-Override (Standard: globale
        Einstellung 'winrm_use_https'). Der Port wird daraus abgeleitet
        (Standard-WinRM-Ports 5986/HTTPS bzw. 5985/HTTP), nicht aus der
        globalen 'winrm_port'-Einstellung -- so koennen einzelne Cluster ohne
        eigenes Zertifikat per HTTP angebunden werden, waehrend andere
        HTTPS nutzen."""
        self._settings = settings
        self._target_host = target_host
        self._use_https = settings.winrm_use_https if use_https is None else use_https

    @property
    def port(self) -> int:
        return 5986 if self._use_https else 5985

    def _session(self, username: str, password: str, *, read_timeout_sec: int = 30, operation_timeout_sec: int = 20) -> winrm.Session:
        scheme = "https" if self._use_https else "http"
        endpoint = f"{scheme}://{self._target_host}:{self.port}/wsman"
        return winrm.Session(
            endpoint,
            auth=(username, password),
            transport=self._settings.winrm_transport,
            server_cert_validation="validate" if self._use_https else "ignore",
            read_timeout_sec=read_timeout_sec,
            operation_timeout_sec=operation_timeout_sec,
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
        # WICHTIG: ConvertTo-Json serialisiert den ClusterNodeState-Enum ohne
        # .ToString() als rohe Ganzzahl (0 = Up), nicht als "Up" -- gegen
        # echte Hardware verifiziert (State kam als "0" zurueck, wurde daher
        # faelschlich als "nicht Up" gewertet). Deshalb hier explizit
        # stringifizieren, analog zu list_vms().
        script = (
            "$cluster = Get-Cluster; "
            "$nodes = Get-ClusterNode | Select-Object Name, @{N='State';E={$_.State.ToString()}}; "
            "[PSCustomObject]@{ ClusterName = $cluster.Name; Nodes = $nodes } | ConvertTo-Json -Depth 4"
        )
        try:
            session = self._session(username, password, read_timeout_sec=15, operation_timeout_sec=10)
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
        # Get-VM liefert nur die auf DIESEM Host lokalen VMs -- fuer eine
        # clusterweite Sicht wird diese Methode daher pro Knoten einzeln
        # aufgerufen (siehe run_vm_discovery), nicht einmalig gegen den CNO.
        # $env:COMPUTERNAME statt $vm.ComputerName, da wir wissen, mit
        # welchem Knoten diese Session tatsaechlich verbunden ist.
        script = (
            "$vms = Get-VM; "
            "$hostName = $env:COMPUTERNAME; "
            "$vms | ForEach-Object { "
            "$vm = $_; "
            "$vhds = @($vm.HardDrives | ForEach-Object { "
            "$info = Get-VHD -Path $_.Path -ErrorAction SilentlyContinue; "
            "[PSCustomObject]@{ Path = $_.Path; SizeBytes = $(if ($info) { $info.Size } else { 0 }); UsedBytes = $(if ($info) { $info.FileSize } else { 0 }) } "
            "}); "
            "[PSCustomObject]@{ Name = $vm.Name; Id = $vm.Id.ToString(); State = $vm.State.ToString(); ComputerName = $hostName; Vhds = $vhds } "
            "} | ConvertTo-Json -Depth 5"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Get-VM fehlgeschlagen: {result.error}")

        raw = json.loads(result.output or "[]")
        entries = raw if isinstance(raw, list) else [raw]
        vms = []
        for e in entries:
            vhds_raw = e.get("Vhds") or []
            vhds_raw = vhds_raw if isinstance(vhds_raw, list) else [vhds_raw]
            vhds = [
                VhdDetail(path=v["Path"], size_bytes=int(v.get("SizeBytes") or 0), used_bytes=int(v.get("UsedBytes") or 0))
                for v in vhds_raw
                if v.get("Path")
            ]
            vms.append(
                VirtualMachineInfo(
                    name=e["Name"], id=e["Id"], state=str(e["State"]), host=e.get("ComputerName", self._target_host), vhds=vhds,
                )
            )
        return vms

    def _node_management_ips(self, session: winrm.Session) -> dict[str, str]:
        """Liefert je Knoten die IP-Adresse im 'ClusterAndClient'-Netzwerk
        (dem Management-Netz, auf dem auch der CNO selbst erreichbar ist).
        Vermeidet DNS-Aufloesung der (oft nur per NetBIOS/AD-DNS bekannten)
        Knoten-Kurznamen -- gegen echte Hardware verifiziert: Knoten-Namen
        waren vom Container aus nicht aufloesbar, die per
        Get-ClusterNetworkInterface ermittelten IPs schon."""
        script = (
            "Get-ClusterNetworkInterface | Where-Object { $_.Network.Role.ToString() -eq 'ClusterAndClient' } "
            "| Select-Object Node, Address | ConvertTo-Json -Depth 3"
        )
        result = self._run_ps(session, script)
        if not result.success:
            return {}
        try:
            raw = json.loads(result.output or "[]")
        except json.JSONDecodeError:
            return {}
        entries = raw if isinstance(raw, list) else [raw]
        return {e["Node"]: e["Address"] for e in entries if e.get("Node") and e.get("Address")}

    def run_vm_discovery(self, username: str, password: str) -> tuple[list[DiscoveryStepResult], HyperVDiscoveryData]:
        """Erkennt alle VMs im Cluster inkl. ihrer VHDs (Pfad/Speicherort +
        Groesse) sowie des Knotens, auf dem sie laufen. Verbindet sich dazu
        NICHT nur zum CNO, sondern zusaetzlich einzeln zu jedem Cluster-
        Knoten: Get-VM liefert nur lokale VMs, und ein 'zweiter Hop' vom CNO
        zu einem Knoten INNERHALB derselben Session wuerde bei NTLM (ohne
        Credential-Delegation) fehlschlagen. Jeder Knoten wird daher per
        eigener, direkter WinRM-Verbindung vom Container aus abgefragt --
        ueber seine Management-IP statt seinen (oft nicht aufloesbaren)
        Namen, siehe _node_management_ips."""
        results: list[DiscoveryStepResult] = []
        data = HyperVDiscoveryData()

        try:
            cno_session = self._session(username, password, read_timeout_sec=15, operation_timeout_sec=10)
            summary = self.get_cluster_summary(username, password)
            results.append(DiscoveryStepResult("login", True, f"Angemeldet an Cluster '{summary.cluster_name}'"))
        except HyperVConnectionError as exc:
            results.append(DiscoveryStepResult("login", False, str(exc)))
            return results, data

        node_ips = self._node_management_ips(cno_session)

        for node in summary.nodes:
            if node.state != "Up":
                results.append(DiscoveryStepResult("vms", False, f"Knoten '{node.name}' übersprungen (Status: {node.state})"))
                continue
            target = node_ips.get(node.name, node.name)
            try:
                node_service = HyperVService(self._settings, target, use_https=self._use_https)
                session = node_service._session(username, password)
                vms = node_service.list_vms(session)
                data.vms.extend(vms)
                results.append(DiscoveryStepResult("vms", True, f"{len(vms)} VM(s) auf '{node.name}' gefunden", len(vms)))
            except Exception as exc:
                results.append(DiscoveryStepResult("vms", False, f"Knoten '{node.name}' ({target}): {exc}"))

        return results, data

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
