"""Minimaler SMB-Dateizugriff fuer die DB-Sicherung (Backlog #66).

Der Container laeuft rootless und kann keine CIFS-Freigaben einbinden --
daher die reine Python-Implementierung smbprotocol (SMB 2/3, NTLM, Signing,
optional Verschluesselung), ohne Kernel-Mount und ohne Zusatzrechte."""

import ntpath
from dataclasses import dataclass

import smbclient
from smbprotocol.exceptions import SMBException


class SmbTargetError(RuntimeError):
    pass


@dataclass
class SmbFile:
    name: str
    size_bytes: int


def split_unc(path: str) -> tuple[str, str]:
    """('fs01', r'\\\\fs01\\backup\\hvnb') -- Server + normalisierter Pfad."""
    normalized = (path or "").strip().replace("/", "\\").rstrip("\\")
    if not normalized.startswith("\\\\"):
        raise SmbTargetError("Pfad muss ein UNC-Pfad sein, z.B. \\\\server\\freigabe\\ordner")
    parts = [p for p in normalized[2:].split("\\") if p]
    if len(parts) < 2:
        raise SmbTargetError("UNC-Pfad braucht mindestens Server und Freigabe, z.B. \\\\server\\freigabe")
    return parts[0], "\\\\" + "\\".join(parts)


class SmbTarget:
    def __init__(self, unc_path: str, username: str, password: str, *, timeout_sec: int = 20):
        self.server, self.root = split_unc(unc_path)
        self._username = username or None
        self._password = password or None
        self._timeout = timeout_sec

    def __enter__(self) -> "SmbTarget":
        try:
            smbclient.register_session(
                self.server, username=self._username, password=self._password, connection_timeout=self._timeout,
            )
            smbclient.makedirs(self.root, exist_ok=True)
        except (SMBException, OSError, ValueError) as exc:
            raise SmbTargetError(f"Verbindung zu {self.root} fehlgeschlagen: {exc}") from exc
        return self

    def __exit__(self, *exc_info) -> None:
        try:
            smbclient.delete_session(self.server)
        except Exception:  # noqa: BLE001 -- Aufraeumen ist best effort
            pass

    def _path(self, name: str) -> str:
        if "\\" in name or "/" in name or name in ("", ".", ".."):
            raise SmbTargetError(f"Ungueltiger Dateiname: {name}")
        return ntpath.join(self.root, name)

    def write(self, name: str, data: bytes) -> None:
        try:
            with smbclient.open_file(self._path(name), mode="wb") as handle:
                handle.write(data)
        except (SMBException, OSError) as exc:
            raise SmbTargetError(f"Schreiben von {name} fehlgeschlagen: {exc}") from exc

    def read(self, name: str) -> bytes:
        try:
            with smbclient.open_file(self._path(name), mode="rb") as handle:
                return handle.read()
        except (SMBException, OSError) as exc:
            raise SmbTargetError(f"Lesen von {name} fehlgeschlagen: {exc}") from exc

    def remove(self, name: str) -> None:
        try:
            smbclient.remove(self._path(name))
        except (SMBException, OSError) as exc:
            raise SmbTargetError(f"Loeschen von {name} fehlgeschlagen: {exc}") from exc

    def list_files(self) -> list[SmbFile]:
        try:
            return [
                SmbFile(name=entry.name, size_bytes=entry.stat().st_size)
                for entry in smbclient.scandir(self.root)
                if entry.is_file()
            ]
        except (SMBException, OSError) as exc:
            raise SmbTargetError(f"Auflisten von {self.root} fehlgeschlagen: {exc}") from exc
