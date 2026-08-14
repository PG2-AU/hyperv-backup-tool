"""Troubleshooting-Logs. Liefert strukturierte Log-Eintraege, die im Frontend
gefiltert, kontextbezogen durchsucht und fuer den Support kopiert werden
koennen.

TODO(iteration): Aktuell In-Memory-Demo-Daten; Anbindung an echtes
Logging-Backend (z.B. strukturierte Log-Datei + DB-Ringpuffer) folgt.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.schemas.log import LogEntry, LogLevel

router = APIRouter(prefix="/api/logs", tags=["logs"])

_now = datetime.now(timezone.utc)
_DEMO_LOGS = [
    LogEntry(timestamp=_now - timedelta(minutes=5), level=LogLevel.INFO, source="scheduler", context="job-001", message="Job 'SQL-Cluster taeglich 02:00' gestartet"),
    LogEntry(timestamp=_now - timedelta(minutes=4, seconds=50), level=LogLevel.INFO, source="hyperv", context="job-001", message="Production Checkpoint 'daily.2026-08-14_0200' auf APP-SQL01 erstellt"),
    LogEntry(timestamp=_now - timedelta(minutes=4, seconds=20), level=LogLevel.INFO, source="netapp", context="job-001", message="Snapshot 'daily.2026-08-14_0200' auf vol_csv1 erstellt"),
    LogEntry(timestamp=_now - timedelta(minutes=3), level=LogLevel.INFO, source="netapp", context="job-001", message="SnapMirror-Update zu svm-hyperv-dr ausgeloest"),
    LogEntry(timestamp=_now - timedelta(minutes=1), level=LogLevel.INFO, source="scheduler", context="job-001", message="Job erfolgreich abgeschlossen"),
    LogEntry(timestamp=_now - timedelta(hours=3), level=LogLevel.ERROR, source="netapp", context="job-002", message="SnapMirror-Update fehlgeschlagen: destination volume offline"),
    LogEntry(timestamp=_now - timedelta(hours=3, seconds=-5), level=LogLevel.WARNING, source="scheduler", context="job-002", message="Starte automatisches Cleanup nach Fehlschlag"),
    LogEntry(timestamp=_now - timedelta(hours=3, seconds=-10), level=LogLevel.INFO, source="hyperv", context="job-002", message="Checkpoints fuer CSV1-Job entfernt"),
]


@router.get("", response_model=list[LogEntry])
def get_logs(
    context: str | None = Query(default=None, description="z.B. job-001 fuer kontextbezogenes Log"),
    level: LogLevel | None = None,
    search: str | None = None,
    user=Depends(require_permission(Permission.LOGS_VIEW)),
) -> list[LogEntry]:
    entries = _DEMO_LOGS
    if context:
        entries = [e for e in entries if e.context == context]
    if level:
        entries = [e for e in entries if e.level == level]
    if search:
        needle = search.lower()
        entries = [e for e in entries if needle in e.message.lower()]
    return sorted(entries, key=lambda e: e.timestamp)
