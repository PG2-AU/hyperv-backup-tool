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

    def list_vms(self, session: winrm.Session) -> list[VirtualMachineInfo]:
        script = (
            "Get-VM | Select-Object Name, Id, State, ComputerName, "
            "@{N='CsvPaths';E={($_.HardDrives).Path}} | ConvertTo-Json -Depth 4"
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
            )
            for e in entries
        ]

    def list_csvs(self, session: winrm.Session) -> list[dict]:
        script = "Get-ClusterSharedVolume | Select-Object Name, SharedVolumeInfo, State | ConvertTo-Json -Depth 4"
        result = self._run_ps(session, script)
        if not result.success:
            raise RuntimeError(f"Get-ClusterSharedVolume fehlgeschlagen: {result.error}")
        raw = json.loads(result.output or "[]")
        return raw if isinstance(raw, list) else [raw]

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
