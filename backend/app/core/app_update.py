"""Update der App per hochgeladenem Release-Paket (Settings > Updates).

Der Container kann sich nicht selbst gegen ein neues Image austauschen -- das
macht der Host. Die App legt deshalb nur das Paket und einen Auftrag in den
Ordner update-inbox des Daten-Volumes; der Host-Dienst (scripts/hvnb-update
--agent-run, per systemd-Timer) holt ihn ab und spielt das Paket mit allen
Pruefungen von hvnb-update ein (Pruefsumme, laufende Backups, DB-Sicherung,
Rueckfall). Dateien im Ordner:

  hvnb-<version>.tar.gz   hochgeladenes Paket
  staged.json             was hochgeladen wurde (von der App)
  request.json            Auftrag "einspielen" (von der App; der Dienst
                          benennt ihn waehrend der Arbeit in request.processing um)
  agent.json              Lebenszeichen des Host-Dienstes
  result.json/result.log  Ergebnis des letzten Einspielens (vom Dienst)
  autoupdate.json         Zustand des Auto-Updates aus Git (hvnb-git-autoupdate)
  settings.json           Schalter "automatische Updates" (von der App)
  registry.json           neueste Version in der Online-Registry (vom Dienst)

Nur im Release-Image verfuegbar (app.core.release.release_info). Ohne
Signatur (Nutzer-Entscheidung 2026-10-08): geprueft wird die SHA-256-
Pruefsumme, die Sicherheit haengt damit am Recht settings:manage."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings

PACKAGE_RE = re.compile(r"^hvnb-(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)\.tar\.gz$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PACKAGE_BYTES = 2 * 1024**3
# Der Host-Dienst meldet sich alle ~30 s; aelter als das gilt er als nicht aktiv.
AGENT_FRESH_SECONDS = 150


def inbox() -> Path:
    return Path(get_settings().update_inbox_dir)


def _read_json(name: str) -> dict | None:
    try:
        data = json.loads((inbox() / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(name: str, data: dict) -> None:
    """Atomar schreiben, damit der Host-Dienst nie eine halbe Datei liest. Ein
    Schluessel je Zeile -- das Host-Skript liest die Felder ohne JSON-Parser."""
    folder = inbox()
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{name}.tmp"
    tmp.write_text(json.dumps(data, indent=0, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(folder / name)


def agent_state() -> dict:
    data = _read_json("agent.json") or {}
    seen = data.get("seen_at")
    active = False
    try:
        if seen:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(seen).replace("Z", "+00:00"))).total_seconds()
            active = -300 <= age <= AGENT_FRESH_SECONDS
    except ValueError:
        pass
    return {"active": active, "last_seen_at": seen, "registry": str(data.get("registry") or "") or None}


def settings() -> dict:
    return _read_json("settings.json") or {}


def update_settings(**values: str) -> None:
    """Einzelne Schalter aendern, die uebrigen behalten."""
    write_json("settings.json", {**settings(), **values})


def registry_state() -> dict | None:
    """Online-Update aus einer Registry -- None, wenn auf dem Server keine
    hinterlegt ist (hvnb-update --set-registry). Zugangsdaten zur Registry hat
    nur der Server."""
    repo = agent_state()["registry"]
    if not repo:
        return None
    check = _read_json("registry.json") or {}
    if check.get("repo") != repo:
        check = {}
    return {
        "repo": repo,
        "latest_version": check.get("latest_version") or None,
        "checked_at": check.get("checked_at") or None,
        "error": check.get("error") or None,
        "auto_enabled": settings().get("registry_auto_update") == "on",
    }


def auto_update_state() -> dict | None:
    """Zustand des Auto-Updates aus Git (scripts/hvnb-git-autoupdate, nur
    Entwicklungsumgebungen) -- None, wenn es auf dem Server nicht eingerichtet
    ist bzw. sich nicht mehr meldet. 'enabled' ist der Schalter aus der GUI."""
    data = _read_json("autoupdate.json")
    if not data:
        return None
    try:
        interval = max(1, int(data.get("interval_minutes") or 5))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(data.get("seen_at")).replace("Z", "+00:00"))).total_seconds()
    except (TypeError, ValueError):
        return None
    # Waehrend eines Builds meldet sich der Dienst laenger nicht.
    if age > max(3 * interval, 30) * 60:
        return None
    return {**data, "enabled": settings().get("auto_update") != "off"}


def set_auto_update(enabled: bool) -> None:
    update_settings(auto_update="on" if enabled else "off")


def staged() -> dict | None:
    data = _read_json("staged.json")
    if not data or not PACKAGE_RE.match(str(data.get("package", ""))):
        return None
    return data if (inbox() / data["package"]).is_file() else None


def pending() -> str | None:
    """'requested' = Auftrag liegt bereit, 'running' = der Host-Dienst arbeitet daran."""
    if (inbox() / "request.processing").exists():
        return "running"
    if (inbox() / "request.json").exists():
        return "requested"
    return None


def last_result() -> dict | None:
    data = _read_json("result.json")
    if not data:
        return None
    try:
        data["log"] = (inbox() / "result.log").read_text(encoding="utf-8", errors="replace")[-6000:]
    except OSError:
        data["log"] = None
    return data


def discard_staged() -> None:
    folder = inbox()
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        if path.name == "staged.json" or path.name.endswith(".part") or PACKAGE_RE.match(path.name) or path.name.endswith(".tar.gz.sha256"):
            path.unlink(missing_ok=True)
