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
class NetworkAdapterDetail:
    name: str
    mac_address: str | None = None
    switch_name: str | None = None
    vlan_id: int | None = None


@dataclass
class VirtualMachineInfo:
    name: str
    id: str
    state: str
    host: str
    vhds: list[VhdDetail] = field(default_factory=list)
    cpu_count: int | None = None
    generation: int | None = None
    memory_startup_bytes: int | None = None
    memory_minimum_bytes: int | None = None
    memory_maximum_bytes: int | None = None
    dynamic_memory_enabled: bool | None = None
    network_adapters: list[NetworkAdapterDetail] = field(default_factory=list)
    pci_devices: list[str] = field(default_factory=list)


@dataclass
class ClusterSharedVolumeInfo:
    name: str
    owner_node: str
    state: str
    volume_path: str
    capacity_bytes: int = 0
    used_bytes: int = 0
    disk_number: int | None = None
    disk_serial_number: str | None = None


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
    csvs: list[ClusterSharedVolumeInfo] = field(default_factory=list)


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

    def connect(self, username: str, password: str, *, read_timeout_sec: int = 30, operation_timeout_sec: int = 20) -> winrm.Session:
        """Oeffentlicher Wrapper um _session fuer Aufrufer ausserhalb dieser
        Klasse (siehe app.api.routes.restore), die mehrere Aufrufe ueber
        dieselbe Verbindung buendeln muessen."""
        return self._session(username, password, read_timeout_sec=read_timeout_sec, operation_timeout_sec=operation_timeout_sec)

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
        # aufgerufen (siehe run_discovery), nicht einmalig gegen den CNO.
        # $env:COMPUTERNAME statt $vm.ComputerName, da wir wissen, mit
        # welchem Knoten diese Session tatsaechlich verbunden ist.
        #
        # Erfasst zusaetzlich zur reinen Inventarisierung auch CPU/RAM/
        # Generation/Netzwerkadapter/PCI-Passthrough-Devices -- fuer die
        # VM-Details im Inventory sowie als Grundlage fuer die pro Backup-Lauf
        # gespeicherte VM-Konfiguration (siehe app.api.routes.jobs
        # trigger_job_run). Get-VMAssignableDevice/Get-VMNetworkAdapterVlan
        # koennen je nach Hyper-V-Version/DDA-Konfiguration fehlschlagen,
        # daher mit -ErrorAction SilentlyContinue bzw. try/catch abgesichert
        # statt den gesamten Discovery-Lauf daran scheitern zu lassen.
        script = (
            "$vms = Get-VM; "
            "$hostName = $env:COMPUTERNAME; "
            "$vms | ForEach-Object { "
            "$vm = $_; "
            "$vhds = @($vm.HardDrives | ForEach-Object { "
            "$info = Get-VHD -Path $_.Path -ErrorAction SilentlyContinue; "
            "[PSCustomObject]@{ Path = $_.Path; SizeBytes = $(if ($info) { $info.Size } else { 0 }); UsedBytes = $(if ($info) { $info.FileSize } else { 0 }) } "
            "}); "
            "$nics = @(Get-VMNetworkAdapter -VM $vm -ErrorAction SilentlyContinue | ForEach-Object { "
            "$vlanId = $null; "
            "try { $vlanId = (Get-VMNetworkAdapterVlan -VMNetworkAdapter $_ -ErrorAction Stop).AccessVlanId } catch {}; "
            "[PSCustomObject]@{ Name = $_.Name; MacAddress = $_.MacAddress; SwitchName = $_.SwitchName; VlanId = $vlanId } "
            "}); "
            "$pci = @(Get-VMAssignableDevice -VM $vm -ErrorAction SilentlyContinue | ForEach-Object { $_.InstancePath }); "
            "[PSCustomObject]@{ "
            "Name = $vm.Name; Id = $vm.Id.ToString(); State = $vm.State.ToString(); ComputerName = $hostName; Vhds = $vhds; "
            "ProcessorCount = $vm.ProcessorCount; Generation = $vm.Generation; "
            "MemoryStartupBytes = $vm.MemoryStartup; MemoryMinimumBytes = $vm.MemoryMinimum; MemoryMaximumBytes = $vm.MemoryMaximum; "
            "DynamicMemoryEnabled = $vm.DynamicMemoryEnabled; NetworkAdapters = $nics; PciDevices = $pci "
            "} "
            "} | ConvertTo-Json -Depth 6"
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
            nics_raw = e.get("NetworkAdapters") or []
            nics_raw = nics_raw if isinstance(nics_raw, list) else [nics_raw]
            nics = [
                NetworkAdapterDetail(
                    name=n.get("Name") or "", mac_address=n.get("MacAddress"),
                    switch_name=n.get("SwitchName"), vlan_id=n.get("VlanId"),
                )
                for n in nics_raw
            ]
            pci_raw = e.get("PciDevices") or []
            pci_devices = pci_raw if isinstance(pci_raw, list) else [pci_raw]
            vms.append(
                VirtualMachineInfo(
                    name=e["Name"], id=e["Id"], state=str(e["State"]), host=e.get("ComputerName", self._target_host), vhds=vhds,
                    cpu_count=e.get("ProcessorCount"), generation=e.get("Generation"),
                    memory_startup_bytes=e.get("MemoryStartupBytes"), memory_minimum_bytes=e.get("MemoryMinimumBytes"),
                    memory_maximum_bytes=e.get("MemoryMaximumBytes"), dynamic_memory_enabled=e.get("DynamicMemoryEnabled"),
                    network_adapters=nics, pci_devices=[p for p in pci_devices if p],
                )
            )
        return vms

    def get_vm_owner_node(self, session: winrm.Session, vm_name: str) -> str | None:
        """Ermittelt LIVE, welcher Cluster-Knoten eine VM aktuell besitzt/
        ausfuehrt -- per Get-ClusterGroup gegen den CNO (liest nur die
        Cluster-Datenbank, Single-Hop wie schon bei list_csvs/
        _node_management_ips), NICHT ueber die ggf. veraltete
        HyperVVm.host_name aus der letzten Discovery. Noetig, weil eine VM
        zwischen zwei Discovery-Laeufen per Live-Migration/Failover auf
        einen anderen Knoten gewandert sein kann -- gegen echten Cluster
        verifiziert: Get-VM auf dem laut (veralteter) Discovery-DB
        'richtigen' Knoten fand die VM nicht mehr ('Hyper-V was unable to
        find a virtual machine'). Liefert None, falls die VM keine eigene
        Cluster-Gruppe hat (z.B. nicht hochverfuegbar) -- Aufrufer sollte
        dann auf die Discovery-DB zurueckfallen."""
        escaped = vm_name.replace("'", "''")
        result = self._run_ps(session, f"(Get-ClusterGroup -Name '{escaped}' -ErrorAction SilentlyContinue).OwnerNode.Name")
        if not result.success:
            return None
        return result.output.strip() or None

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
        # Schluessel bewusst kleingeschrieben: 'Node' aus
        # Get-ClusterNetworkInterface kann in anderer Gross-/Kleinschreibung
        # vorliegen als der Knotenname aus Get-VM/Get-ClusterNode (z.B.
        # 'svAUdemo7-hv103' vs. 'SVAUDEMO7-HV103') -- gegen echten Cluster
        # verifiziert: ein case-sensitiver dict-Lookup lieferte sonst nichts,
        # und resolve_node_address fiel auf den (vom Container aus nicht
        # aufloesbaren) Namen zurueck.
        return {e["Node"].lower(): e["Address"] for e in entries if e.get("Node") and e.get("Address")}

    def run_discovery(self, username: str, password: str) -> tuple[list[DiscoveryStepResult], HyperVDiscoveryData]:
        """Erkennt alle VMs im Cluster inkl. ihrer VHDs (Pfad/Speicherort +
        Groesse) sowie des Knotens, auf dem sie laufen, sowie alle Cluster
        Shared Volumes inkl. Pfad/Groesse/Belegung und (sofern zuordenbar)
        des zugrunde liegenden physischen Datenraegers. Verbindet sich dazu
        NICHT nur zum CNO, sondern zusaetzlich einzeln zu jedem Cluster-
        Knoten: Get-VM liefert nur lokale VMs, und ein 'zweiter Hop' vom CNO
        zu einem Knoten INNERHALB derselben Session wuerde bei NTLM (ohne
        Credential-Delegation) fehlschlagen. Jeder Knoten wird daher per
        eigener, direkter WinRM-Verbindung vom Container aus abgefragt --
        ueber seine Management-IP statt seinen (oft nicht aufloesbaren)
        Namen, siehe _node_management_ips. Die CSVs selbst werden dagegen
        (wie _node_management_ips) als Single-Hop-Abfrage direkt gegen die
        bereits geoeffnete CNO-Session gelesen, da Get-ClusterSharedVolume
        nur die Cluster-Datenbank abfragt."""
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
            target = node_ips.get(node.name.lower(), node.name)
            try:
                node_service = HyperVService(self._settings, target, use_https=self._use_https)
                session = node_service._session(username, password)
                vms = node_service.list_vms(session)
                data.vms.extend(vms)
                results.append(DiscoveryStepResult("vms", True, f"{len(vms)} VM(s) auf '{node.name}' gefunden", len(vms)))
            except Exception as exc:
                results.append(DiscoveryStepResult("vms", False, f"Knoten '{node.name}' ({target}): {exc}"))

        try:
            csvs = self.list_csvs(cno_session)
            data.csvs.extend(csvs)
            results.append(DiscoveryStepResult("csvs", True, f"{len(csvs)} CSV(s) gefunden", len(csvs)))
        except Exception as exc:
            results.append(DiscoveryStepResult("csvs", False, str(exc)))

        return results, data

    def list_csvs(self, session: winrm.Session) -> list[ClusterSharedVolumeInfo]:
        # Single-Hop-Abfrage gegen den CNO (liest nur die Cluster-DB, kein
        # Fan-out zu Knoten noetig). Korrelation zur physischen Disk (und
        # damit zum NetApp-LUN) erfolgt bereits hier in PowerShell: der
        # CSV-Pfad (FriendlyVolumeName, z.B. "C:\ClusterStorage\CSV01\")
        # taucht als einer von mehreren AccessPaths der Partition auf, deren
        # DiskNumber wiederum in Get-Disk auf die SerialNumber verweist --
        # gegen echte Hardware verifiziert: Get-Disk's SerialNumber fuer
        # NetApp-LUN-Disks entspricht exakt ONTAP's eigenem
        # lun.serial_number-REST-Feld (z.B. "80EEm]YMCOeO").
        # WICHTIG: State ist ein Enum (ClusterSharedVolumeState) und muss
        # explizit stringifiziert werden, sonst liefert ConvertTo-Json die
        # rohe Ganzzahl (siehe get_cluster_summary fuer den identischen Bug
        # bei ClusterNodeState).
        script = (
            "$disks = Get-Disk | Select-Object Number, SerialNumber; "
            "$partitions = Get-Partition | Where-Object { $_.AccessPaths } | "
            "Select-Object DiskNumber, @{N='AccessPaths';E={$_.AccessPaths -join '|'}}; "
            "Get-ClusterSharedVolume | ForEach-Object { "
            "$info = $_.SharedVolumeInfo; "
            "$friendlyPath = $info.FriendlyVolumeName; "
            "$part = $partitions | Where-Object { $_.AccessPaths -like ('*' + $friendlyPath + '*') } | Select-Object -First 1; "
            "$disk = $null; "
            "if ($part) { $disk = $disks | Where-Object { $_.Number -eq $part.DiskNumber } | Select-Object -First 1 }; "
            "[PSCustomObject]@{ "
            "Name = $_.Name; "
            "State = $_.State.ToString(); "
            "OwnerNode = $_.OwnerNode.Name; "
            "VolumePath = $friendlyPath; "
            "CapacityBytes = $info.Partition.Size; "
            "FreeBytes = $info.Partition.FreeSpace; "
            "DiskNumber = $(if ($part) { $part.DiskNumber } else { $null }); "
            "DiskSerialNumber = $(if ($disk) { $disk.SerialNumber } else { $null }); "
            "} "
            "} | ConvertTo-Json -Depth 4"
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
                disk_number=e.get("DiskNumber"),
                disk_serial_number=(e.get("DiskSerialNumber") or "").strip() or None,
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

    # --- VM-Restore: Disk anhaengen/ersetzen -------------------------------

    def resolve_node_address(self, cno_session: winrm.Session, node_name: str) -> str:
        """Oeffentlicher Wrapper um _node_management_ips fuer Aufrufer
        ausserhalb dieser Klasse (siehe app.api.routes.restore) -- loest den
        Knotennamen zur Management-IP auf, faellt auf den Namen selbst
        zurueck, falls nicht auflösbar (DNS ggf. vorhanden)."""
        ips = self._node_management_ips(cno_session)
        return ips.get(node_name.lower(), node_name)

    def get_vm_state(self, session: winrm.Session, vm_name: str) -> str:
        result = self._run_ps(session, f"(Get-VM -Name '{vm_name}').State.ToString()")
        if not result.success:
            raise RuntimeError(f"Status von '{vm_name}' konnte nicht ermittelt werden: {result.error}")
        return result.output.strip()

    def stop_vm(self, session: winrm.Session, vm_name: str) -> CommandResult:
        return self._run_ps(session, f"Stop-VM -Name '{vm_name}' -Confirm:$false -ErrorAction Stop")

    def start_vm(self, session: winrm.Session, vm_name: str) -> CommandResult:
        return self._run_ps(session, f"Start-VM -Name '{vm_name}' -Confirm:$false -ErrorAction Stop")

    def attach_vhd(self, session: winrm.Session, vm_name: str, vhd_path: str) -> dict:
        """Haengt eine VHDX als zusaetzliche Disk an die VM. Liefert die
        Controller-Position zurueck (fuer ein spaeteres praezises Abhaengen
        beim Cleanup, siehe restore.py)."""
        escaped = vhd_path.replace("'", "''")
        script = (
            f"Add-VMHardDiskDrive -VMName '{vm_name}' -Path '{escaped}' -Passthru | "
            "Select-Object ControllerType, ControllerNumber, ControllerLocation, Path | ConvertTo-Json"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"VHDX konnte nicht an '{vm_name}' angehaengt werden: {result.error}")
        data = json.loads(result.output)
        return {
            "controller_type": data.get("ControllerType"),
            "controller_number": data.get("ControllerNumber"),
            "controller_location": data.get("ControllerLocation"),
            "path": data.get("Path"),
        }

    # --- Komplette VM-Neuerstellung (geloeschte VM aus Backup) -------------

    def create_vm(self, session: winrm.Session, vm_name: str, generation: int, storage_path: str) -> str:
        """Legt eine neue, voellig leere VM an (keine Disks) -- fuer die
        komplette Neuerstellung einer zuvor geloeschten VM aus einem
        Backup-Lauf (siehe VmRecreateRun/_execute_vm_recreate in
        app.api.routes.restore). Disks werden danach einzeln per attach_vhd
        angehaengt, Hardware/Netzwerk per configure_vm_hardware/
        add_network_adapter. Liefert die neue VM-UUID."""
        escaped_name = vm_name.replace("'", "''")
        escaped_path = storage_path.replace("'", "''")
        script = f"$vm = New-VM -Name '{escaped_name}' -Generation {generation} -Path '{escaped_path}' -NoVHD; $vm.Id.ToString()"
        result = self._run_ps(session, script)
        if not result.success or not result.output.strip():
            raise RuntimeError(f"VM '{vm_name}' konnte nicht angelegt werden: {result.error or result.output}")
        return result.output.strip()

    def configure_vm_hardware(
        self, session: winrm.Session, vm_name: str, cpu_count: int | None,
        memory_startup_bytes: int | None, memory_minimum_bytes: int | None, memory_maximum_bytes: int | None,
        dynamic_memory_enabled: bool | None,
    ) -> None:
        escaped = vm_name.replace("'", "''")
        parts: list[str] = []
        if cpu_count:
            parts.append(f"Set-VMProcessor -VMName '{escaped}' -Count {cpu_count} -ErrorAction Stop; ")
        if memory_startup_bytes:
            mem_args = [f"-StartupBytes {memory_startup_bytes}"]
            if dynamic_memory_enabled and memory_minimum_bytes and memory_maximum_bytes:
                mem_args += ["-DynamicMemoryEnabled $true", f"-MinimumBytes {memory_minimum_bytes}", f"-MaximumBytes {memory_maximum_bytes}"]
            else:
                mem_args.append("-DynamicMemoryEnabled $false")
            parts.append(f"Set-VMMemory -VMName '{escaped}' {' '.join(mem_args)} -ErrorAction Stop; ")
        if not parts:
            return
        result = self._run_ps(session, "".join(parts))
        if not result.success:
            raise RuntimeError(f"Hardware-Konfiguration fuer '{vm_name}' fehlgeschlagen: {result.error}")

    def add_network_adapter(
        self, session: winrm.Session, vm_name: str, switch_name: str, vlan_id: int | None, connected: bool = True,
    ) -> None:
        escaped_vm = vm_name.replace("'", "''")
        escaped_switch = switch_name.replace("'", "''")
        script = f"Add-VMNetworkAdapter -VMName '{escaped_vm}' -SwitchName '{escaped_switch}' -ErrorAction Stop; "
        if vlan_id:
            script += (
                f"Get-VMNetworkAdapter -VMName '{escaped_vm}' | Select-Object -Last 1 | "
                f"Set-VMNetworkAdapterVlan -Access -VlanId {vlan_id} -ErrorAction Stop; "
            )
        if not connected:
            # Fuer einen Side-by-side-Restore (Original laeuft parallel
            # weiter): Adapter bewusst NICHT verbinden, damit die neue VM
            # ohne IP-/MAC-Konflikt hochfaehrt, aber trotzdem konfiguriert
            # (VLAN etc.) und mit einem Klick spaeter verbindbar bleibt.
            script += (
                f"Get-VMNetworkAdapter -VMName '{escaped_vm}' | Select-Object -Last 1 | "
                f"Disconnect-VMNetworkAdapter -ErrorAction Stop"
            )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Netzwerkadapter fuer '{vm_name}' konnte nicht angelegt werden: {result.error}")

    def register_cluster_role(self, cno_session: winrm.Session, vm_name: str) -> None:
        """Registriert eine bereits existierende (aber noch nicht
        hochverfuegbare) VM als Cluster-Rolle -- Single-Hop-Abfrage gegen
        den CNO (Cluster-Datenbank-Operation, wie list_csvs/
        get_vm_owner_node), damit sie ueber Failover verfuegbar ist wie das
        Original."""
        escaped = vm_name.replace("'", "''")
        result = self._run_ps(cno_session, f"Add-ClusterVirtualMachineRole -VMName '{escaped}' -ErrorAction Stop | Out-Null")
        if not result.success:
            raise RuntimeError(f"VM '{vm_name}' konnte nicht als Cluster-Rolle registriert werden: {result.error}")

    def detach_vhd(self, session: winrm.Session, vm_name: str, vhd_path: str) -> CommandResult:
        escaped = vhd_path.replace("'", "''")
        script = (
            f"Get-VMHardDiskDrive -VMName '{vm_name}' | Where-Object {{ $_.Path -eq '{escaped}' }} | "
            "Remove-VMHardDiskDrive -Confirm:$false -ErrorAction Stop"
        )
        return self._run_ps(session, script)

    def delete_file(self, session: winrm.Session, path: str) -> CommandResult:
        escaped = path.replace("'", "''")
        return self._run_ps(session, f"Remove-Item -Path '{escaped}' -Force -ErrorAction Stop")

    def rename_file(self, session: winrm.Session, old_path: str, new_path: str) -> CommandResult:
        """Benennt die wiederhergestellte VHDX im Replace-Modus auf den
        Originalnamen um, nachdem die alte Datei geloescht wurde -- die
        Kopie traegt bis dahin den '_restore_<Zeitstempel>'-Suffix (siehe
        restore.py), damit sie waehrend des Kopierens nicht mit der noch
        vorhandenen alten Datei kollidiert."""
        old_escaped = old_path.replace("'", "''")
        new_escaped = new_path.replace("'", "''")
        return self._run_ps(session, f"Move-Item -Path '{old_escaped}' -Destination '{new_escaped}' -Force -ErrorAction Stop")

    # --- VM-Restore: nativer Windows-iSCSI-Initiator auf dem Restore-Proxy-Host ---
    # Ersetzt die fruehere Linux-Variante (iscsiadm/ntfs-3g/smbclient im
    # Container, siehe Chat-Verlauf) -- der native Microsoft-iSCSI-Initiator
    # auf einem Windows-Host ist fuer diesen Zweck deutlich robuster (kein
    # Bedarf an CAP_SYS_ADMIN/NET_ADMIN/MKNOD, kein devtmpfs-Problem, keine
    # WSL2-Netlink-Inkompatibilitaet).

    def get_initiator_iqn(self, session: winrm.Session) -> str:
        """Stellt sicher, dass der Microsoft-iSCSI-Initiator-Dienst laeuft
        (auf einem frischen Windows Server ist er standardmaessig deaktiviert)
        und liefert die IQN des Hosts fuer die Igroup-/Credentials-Einrichtung
        auf der NetApp-Seite."""
        script = (
            "if ((Get-Service -Name MSiSCSI).Status -ne 'Running') { Start-Service MSiSCSI }; "
            "Set-Service -Name MSiSCSI -StartupType Automatic; "
            "(Get-InitiatorPort | Select-Object -First 1 -ExpandProperty NodeAddress)"
        )
        result = self._run_ps(session, script)
        if not result.success or not result.output.strip():
            raise RuntimeError(f"iSCSI-Initiator konnte nicht ermittelt werden: {result.error or result.output}")
        return result.output.strip()

    def iscsi_connect(self, session: winrm.Session, portal_address: str, portal_port: int, target_iqn: str) -> None:
        script = (
            f"New-IscsiTargetPortal -TargetPortalAddress '{portal_address}' -TargetPortalPortNumber {portal_port} "
            "-ErrorAction SilentlyContinue | Out-Null; "
            f"Connect-IscsiTarget -NodeAddress '{target_iqn}' -TargetPortalAddress '{portal_address}' "
            f"-TargetPortalPortNumber {portal_port} -IsPersistent $false -IsMultipathEnabled $false -ErrorAction Stop | Out-Null"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"iSCSI-Verbindung fehlgeschlagen: {result.error}")

    def iscsi_disconnect(self, session: winrm.Session, target_iqn: str) -> None:
        """Best-effort -- wird auch beim Cleanup nach einem Fehler
        aufgerufen, daher kein Raise bei Fehlschlag."""
        self._run_ps(
            session,
            f"Disconnect-IscsiTarget -NodeAddress '{target_iqn}' -Confirm:$false -ErrorAction SilentlyContinue",
        )

    def find_disk_by_serial(self, session: winrm.Session, serial: str, timeout_sec: int = 30) -> int:
        """Pollt Get-Disk, bis die per Seriennummer identifizierte, per iSCSI
        neu verbundene Disk erscheint. Get-Disk's SerialNumber entspricht
        exakt ONTAP's lun.serial_number (siehe list_csvs -- dieselbe
        Korrelation wird dort bereits fuer die Discovery genutzt, hier nur
        mit Poll-Schleife statt einmaliger Abfrage)."""
        escaped = serial.replace("'", "''")
        script = (
            f"1..{timeout_sec} | ForEach-Object {{ "
            f"$d = Get-Disk | Where-Object {{ ($_.SerialNumber -replace '\\s','') -eq '{escaped}' }} "
            "| Select-Object -First 1 -ExpandProperty Number; "
            "if ($null -ne $d) { $d; break }; Start-Sleep -Seconds 1 "
            "}"
        )
        result = self._run_ps(session, script)
        output = result.output.strip()
        if not result.success or not output:
            raise RuntimeError(f"Disk mit Seriennummer '{serial}' nicht gefunden (Timeout). {result.error or ''}".strip())
        return int(output.splitlines()[-1])

    def prepare_data_partition_path(self, session: winrm.Session, disk_number: int, mount_dir: str) -> str:
        """Bringt die Disk online/beschreibbar, ermittelt die groessere der
        beiden Partitionen (die kleine 'Reserved'-Partition ist immer die
        erste, die Datenpartition die groesste -- identisches Muster wie
        beim fruehen Linux-Ansatz) und haengt sie unter mount_dir ein."""
        escaped_dir = mount_dir.replace("'", "''")
        script = (
            f"$disk = Get-Disk -Number {disk_number}; "
            f"if ($disk.IsOffline) {{ Set-Disk -Number {disk_number} -IsOffline $false }}; "
            f"if ($disk.IsReadOnly) {{ Set-Disk -Number {disk_number} -IsReadOnly $false }}; "
            f"$part = Get-Partition -DiskNumber {disk_number} | Where-Object {{ $_.Type -eq 'Basic' }} "
            "| Sort-Object Size -Descending | Select-Object -First 1; "
            "if (-not $part) { throw 'Keine Datenpartition gefunden' }; "
            f"if (-not (Test-Path '{escaped_dir}')) {{ New-Item -ItemType Directory -Path '{escaped_dir}' -Force | Out-Null }}; "
            f"Add-PartitionAccessPath -DiskNumber {disk_number} -PartitionNumber $part.PartitionNumber -AccessPath '{escaped_dir}' "
            "-ErrorAction Stop; "
            f"'{escaped_dir}'"
        )
        result = self._run_ps(session, script)
        if not result.success or not result.output.strip():
            raise RuntimeError(f"Partition konnte nicht eingebunden werden: {result.error or result.output}")
        return result.output.strip()

    def release_disk(self, session: winrm.Session, disk_number: int, mount_dir: str) -> None:
        """Best-effort -- wird auch beim Cleanup nach einem Fehler
        aufgerufen, daher kein Raise bei Fehlschlag."""
        escaped_dir = mount_dir.replace("'", "''")
        script = (
            f"Get-Partition -DiskNumber {disk_number} -ErrorAction SilentlyContinue | ForEach-Object {{ "
            f"Remove-PartitionAccessPath -DiskNumber {disk_number} -PartitionNumber $_.PartitionNumber "
            f"-AccessPath '{escaped_dir}' -ErrorAction SilentlyContinue }}; "
            f"Remove-Item -Path '{escaped_dir}' -Force -ErrorAction SilentlyContinue; "
            f"Set-Disk -Number {disk_number} -IsOffline $true -ErrorAction SilentlyContinue"
        )
        self._run_ps(session, script)

    def get_file_size(self, session: winrm.Session, path: str) -> int:
        escaped = path.replace("'", "''")
        result = self._run_ps(session, f"(Get-Item -Path '{escaped}').Length")
        if not result.success:
            raise RuntimeError(f"Groesse von '{path}' konnte nicht ermittelt werden: {result.error}")
        return int(result.output.strip())

    def copy_file_to_share(
        self, session: winrm.Session, source_path: str, node_address: str,
        remote_dir: str, remote_filename: str, share_username: str, share_password: str,
    ) -> int:
        """Kopiert eine Datei vom Restore-Proxy-Host auf die administrative
        C$-Freigabe eines Hyper-V-Knotens. Bindet das Ziel explizit mit
        eigenen Zugangsdaten ein statt die WinRM-Sitzungsidentitaet zu
        delegieren -- WinRM/NTLM erlaubt keine Weitergabe der eingehenden
        Authentifizierung an einen dritten Host ('Double-Hop'-Problem).

        Nutzt dafuer 'net use' statt New-SmbMapping: Letzteres ist
        CIM/WMI-basiert und schlaegt innerhalb einer per WinRM/NTLM
        aufgebauten PowerShell-Sitzung reproduzierbar mit 'A specified logon
        session does not exist' (Windows-Fehler 1312) fehl, da ein
        NTLM-Netzwerklogon-Token keine weiteren Logon-Sessions erzeugen darf
        -- genau das braucht New-SmbMapping intern (verifiziert live gegen
        einen echten Restore-Proxy-Host). net.exe umgeht das, da es ohne CIM
        auskommt.

        HINWEIS: Ein frueherer Versuch, hierueber per Start-Job +
        Zwischenausgaben einen Live-Fortschritt zu liefern, wurde
        zurueckgenommen -- WinRM/WinRS puffert die Ausgabe eines Kommandos
        und liefert sie oft erst gebuendelt am Ende (Fortschritt sprang
        sichtbar von 0% auf 100%, statt live mitzulaufen), und der separate
        Job-Prozess konnte die per iSCSI gemountete Quelle beim naechsten
        Live-Test nicht zuverlaessig finden ('Cannot find path', obwohl sie
        existierte). Kopiert deshalb wieder synchron in einem Schritt.
        Prueft die Zielgroesse nach dem Kopieren (Copy-Item meldet Erfolg
        nicht zuverlaessig genug bei Netzwerkproblemen)."""
        escaped_src = source_path.replace("'", "''")
        escaped_dir = remote_dir.replace("'", "''")
        escaped_file = remote_filename.replace("'", "''")
        escaped_user = share_username.replace("'", "''")
        escaped_pw = share_password.replace("'", "''")
        share = f"\\\\{node_address}\\C$"
        dest = f"{share}\\{escaped_dir}\\{escaped_file}"
        script = (
            f"$share = '{share}'; "
            "net use $share /delete /y 2>&1 | Out-Null; "
            f"net use $share '{escaped_pw}' /user:'{escaped_user}' /persistent:no 2>&1 | Out-Null; "
            "if ($LASTEXITCODE -ne 0) { throw \"net use fehlgeschlagen (Exit $LASTEXITCODE)\" }; "
            "try { "
            # Zielverzeichnis kann bei einer abweichenden Ziel-CSV (siehe
            # destination_csv_name bei der Side-by-side-VM-Wiederherstellung)
            # noch nicht existieren -- New-Item -Force ist idempotent, legt
            # fehlende Ordner an und tut sonst nichts.
            f"New-Item -ItemType Directory -Force -Path '{share}\\{escaped_dir}' -ErrorAction Stop | Out-Null; "
            f"Copy-Item -Path '{escaped_src}' -Destination '{dest}' -Force -ErrorAction Stop; "
            f"(Get-Item -Path '{dest}').Length "
            "} finally { "
            "net use $share /delete /y 2>&1 | Out-Null "
            "}"
        )
        result = self._run_ps(session, script)
        if not result.success or not result.output.strip():
            raise RuntimeError(f"Kopieren auf '{node_address}' fehlgeschlagen: {result.error or result.output}")
        try:
            remote_size = int(result.output.strip().splitlines()[-1])
        except ValueError as exc:
            raise RuntimeError(f"Unerwartete Antwort beim Kopieren: {result.output}") from exc
        source_size = self.get_file_size(session, source_path)
        if source_size != remote_size:
            raise RuntimeError(f"Groessenabweichung nach Kopieren: Quelle {source_size} Bytes, Ziel {remote_size} Bytes")
        return remote_size

    # --- Datei-Restore: VHDX direkt auf dem Restore-Proxy-Host mounten und
    # durchsuchen, statt sie erst auf eine CSV zu kopieren und an eine VM
    # anzuhaengen (siehe app.api.routes.file_restore). Die geklonte LUN wird
    # wie beim normalen Restore ueber prepare_data_partition_path als
    # Ordner eingebunden -- darin liegt die VHDX-Datei selbst, die dann per
    # Mount-VHD eine Ebene tiefer erneut gemountet wird; prepare_data_
    # partition_path/release_disk greifen dafuer unveraendert, nur mit der
    # Disk-Nummer der virtuellen (VHD-)Disk statt der rohen Klon-Disk. -----

    def check_vhd_mount_available(self, session: winrm.Session) -> bool:
        """Prueft, ob Mount-DiskImage/Dismount-DiskImage (Storage-Modul)
        auf diesem Host verfuegbar sind -- Bestandteil von Windows Server
        seit 2012, daher praktisch immer vorhanden. Live gegen den echten
        Restore-Proxy-Host verifiziert wurde dabei ein wichtiger Irrtum
        aufgedeckt: die urspruenglich hierfuer vorgesehenen Mount-VHD/
        Dismount-VHD (Hyper-V-PowerShell-Modul) scheitern auf einem Host
        OHNE echte Hyper-V-Rolle mit 'could not access an expected WMI
        class' -- das Cmdlet braucht den Virtual Machine Management
        Service, nicht nur das Verwaltungsmodul. Mount-DiskImage nutzt
        stattdessen den Windows-eigenen Virtual-Disk-Dienst (denselben,
        den auch 'diskpart attach vdisk' verwendet) und funktioniert daher
        auch auf einem reinen iSCSI-Proxy-Host ohne Hyper-V-Rolle."""
        result = self._run_ps(session, "[bool](Get-Command Mount-DiskImage -ErrorAction SilentlyContinue)")
        return result.success and result.output.strip().lower() == "true"

    def mount_vhd(self, session: winrm.Session, vhd_path: str, read_only: bool = True) -> int:
        """Mountet eine VHDX schreibgeschuetzt (Standard) ueber den
        Windows-eigenen Virtual-Disk-Dienst (Storage-Modul, siehe
        check_hyperv_powershell_available) und liefert die Disk-Nummer der
        dadurch entstehenden virtuellen Disk -- diese wird anschliessend
        wie eine normale Disk per prepare_data_partition_path eingebunden.
        Read-only, da der Restore lediglich lesend Dateien entnehmen soll;
        vermeidet ausserdem Schreibsperren-Konflikte."""
        escaped = vhd_path.replace("'", "''")
        access = "ReadOnly" if read_only else "ReadWrite"
        script = (
            f"Mount-DiskImage -ImagePath '{escaped}' -Access {access} -PassThru -ErrorAction Stop | "
            "Get-DiskImage | Get-Disk | Select-Object -ExpandProperty Number"
        )
        result = self._run_ps(session, script)
        output = result.output.strip()
        if not result.success or not output:
            raise RuntimeError(f"VHDX '{vhd_path}' konnte nicht gemountet werden: {result.error or result.output}")
        return int(output.splitlines()[-1])

    def dismount_vhd(self, session: winrm.Session, vhd_path: str) -> None:
        """Best-effort -- wird auch beim Cleanup nach einem Fehler
        aufgerufen, daher kein Raise bei Fehlschlag."""
        escaped = vhd_path.replace("'", "''")
        self._run_ps(session, f"Dismount-DiskImage -ImagePath '{escaped}' -ErrorAction SilentlyContinue")

    def prepare_vhd_partition_path(self, session: winrm.Session, disk_number: int, mount_dir: str) -> str:
        """Bindet die Datenpartition einer per mount_vhd gemounteten VHDX in
        einen Ordner ein -- Gegenstueck zu prepare_data_partition_path fuer
        den rohen LUN-Klon-Datentraeger, aber bewusst NICHT identisch:

        1. Der rohe Klon-Datentraeger ist immer eine GPT-formatierte
           Cluster-Shared-Volume-Disk (Type 'Basic'). Eine VHDX kann
           dagegen beliebigen Gast-Inhalt haben -- z.B. MBR-partitioniert
           (Type 'IFS' statt 'Basic', live gegen eine per diskpart
           formatierte Test-VHDX verifiziert). Filterung erfolgt daher
           per Ausschluss bekannter Nicht-Datenpartitionen (EFI-System,
           MSR/Reserved, Recovery) statt auf einen einzelnen erwarteten
           Typ zu pruefen.
        2. mount_vhd mountet bewusst read-only (siehe dort) -- anders als
           beim Klon-Datentraeger wird IsReadOnly hier NICHT zurueckgesetzt,
           das wuerde dem Zweck des read-only Mounts widersprechen."""
        escaped_dir = mount_dir.replace("'", "''")
        script = (
            f"$disk = Get-Disk -Number {disk_number}; "
            f"if ($disk.IsOffline) {{ Set-Disk -Number {disk_number} -IsOffline $false }}; "
            f"$part = Get-Partition -DiskNumber {disk_number} | "
            "Where-Object { $_.Type -notin @('Reserved','System','Recovery') } "
            "| Sort-Object Size -Descending | Select-Object -First 1; "
            "if (-not $part) { throw 'Keine Datenpartition gefunden' }; "
            f"if (-not (Test-Path '{escaped_dir}')) {{ New-Item -ItemType Directory -Path '{escaped_dir}' -Force | Out-Null }}; "
            f"Add-PartitionAccessPath -DiskNumber {disk_number} -PartitionNumber $part.PartitionNumber -AccessPath '{escaped_dir}' "
            "-ErrorAction Stop; "
            f"'{escaped_dir}'"
        )
        result = self._run_ps(session, script)
        if not result.success or not result.output.strip():
            raise RuntimeError(f"VHDX-Partition konnte nicht eingebunden werden: {result.error or result.output}")
        return result.output.strip()

    def list_directory(self, session: winrm.Session, path: str) -> list[dict]:
        """Listet den Inhalt eines Verzeichnisses (nur eine Ebene, nicht
        rekursiv) fuer den Datei-Browser in der GUI. Gleiches defensives
        JSON-Array-Handling wie list_vms/list_csvs: ConvertTo-Json liefert
        bei genau einem Treffer ein einzelnes Objekt statt eines Arrays, bei
        keinem Treffer eine leere Zeichenkette."""
        # LastWriteTime muss explizit als ISO-8601-String ausgegeben werden
        # (.ToString('o')) -- sonst liefert ConvertTo-Json das .NET-eigene
        # '/Date(<ms>)/'-Format statt eines direkt parsbaren Zeitstempels
        # (live gegen den echten Restore-Proxy-Host verifiziert, gleiche
        # Kategorie Bug wie bei Enums, siehe get_cluster_summary).
        escaped = path.replace("'", "''")
        script = (
            f"Get-ChildItem -Force -Path '{escaped}' -ErrorAction Stop | "
            "Select-Object Name, @{N='IsDirectory';E={$_.PSIsContainer}}, Length, "
            "@{N='ModifiedAt';E={$_.LastWriteTime.ToString('o')}} | "
            "ConvertTo-Json -Depth 3"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Verzeichnis '{path}' konnte nicht gelesen werden: {result.error}")
        output = result.output.strip()
        if not output:
            return []
        raw = json.loads(output)
        entries = raw if isinstance(raw, list) else [raw]
        return [
            {
                "name": e.get("Name"),
                "is_directory": bool(e.get("IsDirectory")),
                "size_bytes": int(e["Length"]) if e.get("Length") is not None else None,
                "modified_at": e.get("ModifiedAt"),
            }
            for e in entries
            if e.get("Name")
        ]

    def copy_paths(self, session: winrm.Session, source_paths: list[str], destination_dir: str) -> None:
        """Kopiert mehrere Dateien/Ordner (rekursiv) vom gemounteten
        VHDX-Dateisystem in ein Zielverzeichnis auf dem Restore-Proxy-Host.
        Jedes ausgewaehlte Element landet als eigener Datei-/Ordnername
        unter destination_dir (keine flache Ablage, keine
        Namens-Kollisionsbehandlung noetig, da destination_dir pro
        Kopiervorgang frei waehlbar ist)."""
        escaped_dest = destination_dir.replace("'", "''")
        copy_cmds = "; ".join(
            f"Copy-Item -Path '{p.replace(chr(39), chr(39) * 2)}' -Destination '{escaped_dest}' -Recurse -Force -ErrorAction Stop"
            for p in source_paths
        )
        script = (
            f"if (-not (Test-Path '{escaped_dest}')) {{ New-Item -ItemType Directory -Path '{escaped_dest}' -Force | Out-Null }}; "
            f"{copy_cmds}"
        )
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Kopieren nach '{destination_dir}' fehlgeschlagen: {result.error}")
