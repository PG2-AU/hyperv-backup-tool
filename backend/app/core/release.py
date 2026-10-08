"""Versionsangaben der laufenden Installation.

Zwei Auslieferungsarten: das Release-Image (docker/Dockerfile.release) bringt
/opt/app/VERSION.json und CHANGELOG.json mit (von scripts/build-release.sh aus
git erzeugt -- im Image gibt es kein .git und kein git); die bisherige
Auslieferung per git-Checkout im Container wird weiterhin ueber git selbst
abgefragt, bis alle Umgebungen umgestellt sind."""

import json
import subprocess
from pathlib import Path

APP_DIR = Path("/opt/app")
VERSION_FILE = APP_DIR / "VERSION.json"
CHANGELOG_FILE = APP_DIR / "CHANGELOG.json"


def release_info() -> dict | None:
    """Inhalt von VERSION.json (version, commit, commit_count, built_at) --
    None, wenn die App nicht aus einem Release-Image laeuft."""
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _git(*args: str, timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(APP_DIR), *args], capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    return result.stdout if result.returncode == 0 else None


def current_commit() -> tuple[str | None, int | None]:
    """(Commit-Hash, Anzahl Commits) der laufenden Version."""
    info = release_info()
    if info is not None:
        count = info.get("commit_count")
        return (info.get("commit") or None), (count if isinstance(count, int) else None)
    commit = (_git("rev-parse", "HEAD") or "").strip() or None
    count_text = (_git("rev-list", "--count", "HEAD") or "").strip()
    return commit, (int(count_text) if count_text.isdigit() else None)


def changelog(limit: int) -> list[dict]:
    """Letzte Commits, neuester zuerst: hash, short_hash, date, subject, body."""
    try:
        entries = json.loads(CHANGELOG_FILE.read_text(encoding="utf-8"))
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)][: max(0, limit)]
    except (OSError, ValueError):
        pass
    # Feld-/Datensatztrenner als ASCII-Steuerzeichen statt z.B. '|': Commit-
    # Nachrichten enthalten laengere Freitext-Absaetze mit Sonderzeichen.
    field_sep, record_sep = "\x1f", "\x1e"
    output = _git(
        "log", f"-n{limit}", f"--pretty=format:%H{field_sep}%h{field_sep}%ad{field_sep}%s{field_sep}%b{record_sep}", "--date=iso-strict",
        timeout=10,
    )
    commits: list[dict] = []
    for record in (output or "").split(record_sep):
        parts = record.strip("\n").split(field_sep)
        if len(parts) < 4 or not parts[0]:
            continue
        body = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
        commits.append({"hash": parts[0], "short_hash": parts[1], "date": parts[2], "subject": parts[3], "body": body})
    return commits
