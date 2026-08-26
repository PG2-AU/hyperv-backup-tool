"""Lokale Restore-Ausfuehrung im Container: iSCSI-Login zur geklonten LUN,
Partition mounten, VHDX per SMB auf die Ziel-CSV kopieren.

Laeuft komplett lokal im Backend-Prozess (subprocess), NICHT per WinRM/REST --
siehe app.api.routes.restore_infra fuer die Netzwerk-/Paket-/Rechte-
Voraussetzungen (iSCSI-Initiator, ntfs-3g, smbclient, CAP_SYS_ADMIN). Jede
Funktion hier wurde gegen die echte Infrastruktur verifiziert (siehe
Chat-Verlauf): LUN-Klon-Mapping -> iscsiadm-Login ueber die dedizierte
Restore-LIF -> Partition automatisch vom Kernel erkannt (kein kpartx noetig
bei echten SCSI-Disks mit GPT) -> ntfs-3g-Mount -> smbclient-Put auf die
administrative C$-Freigabe des Hyper-V-Knotens.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class RestoreExecutionError(Exception):
    """Ein lokaler Restore-Schritt (iSCSI/Mount/SMB) ist fehlgeschlagen."""


def _run(cmd: list[str], timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RestoreExecutionError(f"Zeitueberschreitung bei '{' '.join(cmd)}': {exc}") from exc
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip() or f"Befehl fehlgeschlagen: {' '.join(cmd)}"
        raise RestoreExecutionError(message[:1000])
    return result


def iscsi_login(portal_ip: str, portal_port: int, target_iqn: str) -> None:
    _run(["iscsiadm", "-m", "discovery", "-t", "sendtargets", "-p", f"{portal_ip}:{portal_port}"], timeout=30)
    _run(["iscsiadm", "-m", "node", "-T", target_iqn, "-p", f"{portal_ip}:{portal_port}", "--login"], timeout=30)


def iscsi_logout(portal_ip: str, portal_port: int, target_iqn: str) -> None:
    """Best-effort -- wird auch beim Cleanup nach einem Fehler aufgerufen,
    daher kein Raise bei Fehlschlag."""
    _run(
        ["iscsiadm", "-m", "node", "-T", target_iqn, "-p", f"{portal_ip}:{portal_port}", "--logout"],
        timeout=30, check=False,
    )


def _read_vpd_serial(device_name: str) -> str | None:
    """Liest die SCSI-Seriennummer direkt aus der VPD-Page 0x80 im sysfs
    (/sys/block/<dev>/device/vpd_pg80), ohne udev/lsblk-Metadaten -- der
    Container laeuft ohne udevd, daher liefert 'lsblk -o SERIAL' hier immer
    NULL (gegen echte Hardware verifiziert: eine echte NetApp-LUN exponiert
    vpd_pg80, rein virtuelle Container-Root-Disks dagegen nicht). Format:
    Byte 0-1 Header, Byte 3 Laenge, danach die ASCII-Seriennummer."""
    path = Path(f"/sys/block/{device_name}/device/vpd_pg80")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 4:
        return None
    length = raw[3]
    return raw[4 : 4 + length].decode("ascii", errors="replace").strip()


def find_disk_by_serial(serial: str, timeout_sec: float = 30.0) -> str:
    """Pollt sysfs, bis die per Seriennummer identifizierte Disk erscheint --
    nach dem iSCSI-Login kann die Geraeteerkennung einen Moment dauern. Gegen
    echte Hardware verifiziert: dieselbe LUN kann kurzzeitig als Pfad-Artefakt
    doppelt auftauchen (einmal ohne zugewiesenen Geraetenamen); es wird der
    tatsaechliche Block-Geraetename (z.B. 'sde') zurueckgegeben."""
    deadline = time.time() + timeout_sec
    last_error: str | None = None
    while time.time() < deadline:
        try:
            result = _run(["lsblk", "-J", "-o", "NAME"], timeout=10)
            data = json.loads(result.stdout)
            for dev in data.get("blockdevices", []):
                name = dev["name"]
                if _read_vpd_serial(name) == serial:
                    return f"/dev/{name}"
        except RestoreExecutionError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RestoreExecutionError(f"Disk mit Seriennummer '{serial}' nicht gefunden (Timeout). {last_error or ''}".strip())


def find_largest_partition(device_path: str, timeout_sec: float = 15.0) -> str:
    """Wartet, bis der Kernel die Partitionen der Disk erkannt hat, und
    waehlt die groesste (die kleine 'Reserved'-Partition ist immer die
    erste, die Datenpartition die groesste -- gegen echte Hardware
    verifiziert)."""
    device_name = Path(device_path).name
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = _run(["lsblk", "-J", "-o", "NAME,SIZE,TYPE", device_path], timeout=10, check=False)
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                devices = data.get("blockdevices", [])
                partitions = devices[0].get("children", []) if devices else []
                candidates = [p for p in partitions if p.get("type") == "part"]
                if candidates:
                    largest = max(candidates, key=lambda p: p.get("size", 0))
                    return f"/dev/{largest['name']}"
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        time.sleep(1)
    raise RestoreExecutionError(f"Keine Partitionen auf '{device_name}' gefunden (Timeout).")


def mount_ntfs(partition_path: str, mount_point: str) -> None:
    Path(mount_point).mkdir(parents=True, exist_ok=True)
    _run(["ntfs-3g", "-o", "ro", partition_path, mount_point], timeout=30)


def unmount(mount_point: str) -> None:
    _run(["umount", mount_point], timeout=15, check=False)


def _split_domain_user(username: str) -> tuple[str | None, str]:
    if "\\" in username:
        domain, user = username.split("\\", 1)
        return domain, user
    if "@" in username:
        user, domain = username.split("@", 1)
        return domain, user
    return None, username


@dataclass
class SmbCopyResult:
    remote_size_bytes: int | None


def copy_via_smb(
    local_path: str, node_address: str, username: str, password: str,
    remote_dir: str, remote_filename: str,
) -> SmbCopyResult:
    """Kopiert eine lokale Datei per SMB auf die administrative C$-Freigabe
    eines Hyper-V-Knotens. `remote_dir` ist ein Windows-Pfad ohne
    Laufwerksbuchstaben, z.B. 'ClusterStorage\\CSV01\\Test'. Prueft danach
    per separatem 'ls', dass die Datei mit plausibler Groesse angekommen ist
    (smbclients Exit-Code allein ist kein verlaessliches Erfolgssignal, siehe
    Chat-Verlauf: interne dskattr-Abfragen koennen NT_STATUS-Fehler werfen,
    obwohl der eigentliche PUT erfolgreich war)."""
    domain, user = _split_domain_user(username)
    local_dir = str(Path(local_path).parent)
    local_name = Path(local_path).name
    local_size = Path(local_path).stat().st_size

    base_cmd = ["smbclient", f"//{node_address}/C$", "-U", f"{user}%{password}"]
    if domain:
        base_cmd += ["-W", domain]

    put_script = f"cd {remote_dir}; lcd {local_dir}; put {local_name} {remote_filename}"
    _run(base_cmd + ["-c", put_script], timeout=3600, check=False)

    verify_script = f"cd {remote_dir}; ls {remote_filename}"
    verify = _run(base_cmd + ["-c", verify_script], timeout=30, check=False)
    output = verify.stdout + verify.stderr
    if "NT_STATUS_NO_SUCH_FILE" in output or remote_filename not in output:
        raise RestoreExecutionError(f"Datei '{remote_filename}' wurde nicht auf dem Ziel gefunden: {output[:500]}")

    remote_size: int | None = None
    for token in output.split():
        if token.isdigit() and int(token) == local_size:
            remote_size = int(token)
            break
    if remote_size is None:
        raise RestoreExecutionError(
            f"Datei '{remote_filename}' angekommen, aber Groesse stimmt nicht mit der Quelle ueberein "
            f"(erwartet {local_size} Bytes). Ausgabe: {output[:500]}"
        )
    return SmbCopyResult(remote_size_bytes=remote_size)
