"""Aenderungsprotokoll (Backlog #84, Grundlage fuer den Report "Audit-Trail"):
ASGI-Middleware, die jede aendernde API-Anfrage (POST/PUT/PATCH/DELETE) eines
angemeldeten Benutzers als AuditEvent ablegt -- Wer, Wann, Bereich, Aktion,
betroffenes Objekt, Ergebnis (HTTP-Status).

Bewusst zentral statt je Endpunkt: neue Endpunkte sind automatisch erfasst
(Bereich/Aktion dann aus dem Pfad abgeleitet, bis ROUTES einen Eintrag
bekommt). Reine Pruef-/Lese-Aufrufe per POST (Verbindungstests, Vorschauen,
Suchen) stehen in SKIP. Der Anfrage-Body wird nie gespeichert -- nur
einzelne Namensfelder daraus (BODY_NAME_KEYS) als Ziel, Passwoerter o.ae.
kommen so nie ins Protokoll. Das Ziel einer ID im Pfad wird VOR der
Ausfuehrung aufgeloest, damit auch geloeschte Objekte ihren Namen behalten.

Fehler im Protokollieren duerfen eine Anfrage nie stoeren -- alles in
try/except, im Zweifel fehlt der Eintrag."""

import json
import logging
import re

import anyio

from app.core.security import decode_access_token

log = logging.getLogger(__name__)

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY = 1024 * 1024

# Nur pruefen/lesen, aendert nichts.
SKIP = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/ad-config/test"),
    ("POST", "/api/email-config/test"),
    ("POST", "/api/kerberos-config/test"),
    ("POST", "/api/kerberos-config/detect"),
    ("POST", "/api/db-backup/test"),
    ("POST", "/api/db-backup/restore/preview"),
    ("POST", "/api/db-backup/restore/upload/preview"),
    ("POST", "/api/config-transfer/import/preview"),
    ("POST", "/api/hyperv/clusters/check-reachability"),
    ("POST", "/api/hyperv/clusters/{cluster_id}/verify"),
    ("POST", "/api/netapp/clusters/{cluster_id}/verify"),
    ("POST", "/api/resource-groups/check-snapmirror"),
    ("POST", "/api/restore-infra/configs/{config_id}/check"),
    ("POST", "/api/users/ad-search"),
    ("POST", "/api/winrm-certs/build-bundle"),
    ("POST", "/api/winrm-certs/setup-script"),
}

ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    ("PUT", "/api/ad-config"): ("Active Directory", "Einstellungen geändert"),
    ("POST", "/api/alerts/{alert_id}/allow-collision"): ("Alarm", "Zeitplan-Kollision erlaubt"),
    ("POST", "/api/alerts/{alert_id}/dismiss"): ("Alarm", "quittiert"),
    ("DELETE", "/api/alerts/allowed-collisions/{allowed_id}"): ("Alarm", "erlaubte Kollision entfernt"),
    ("POST", "/api/alerts/backup-runs/{run_id}/dismiss"): ("Alarm", "Backup-Fehler quittiert"),
    ("PUT", "/api/alerts/config"): ("Alarm", "Einstellungen geändert"),
    ("POST", "/api/alerts/recheck"): ("Alarm", "Prüfung angestoßen"),
    ("POST", "/api/config-transfer/import"): ("Konfiguration", "importiert"),
    ("POST", "/api/csv-create"): ("CSV", "Anlage gestartet"),
    ("POST", "/api/csv-create/runs/{run_id}/keep"): ("CSV", "Teilergebnis behalten"),
    ("POST", "/api/csv-create/runs/{run_id}/rollback"): ("CSV", "Anlage zurückgerollt"),
    ("POST", "/api/csv-delete"): ("CSV", "Löschen gestartet"),
    ("POST", "/api/csv-resize"): ("CSV", "Vergrößern gestartet"),
    ("PUT", "/api/db-backup"): ("DB-Sicherung", "Einstellungen geändert"),
    ("POST", "/api/db-backup/restore"): ("DB-Sicherung", "Datenbank wiederhergestellt"),
    ("POST", "/api/db-backup/restore/upload"): ("DB-Sicherung", "Datenbank aus Upload wiederhergestellt"),
    ("POST", "/api/db-backup/run"): ("DB-Sicherung", "Sicherung gestartet"),
    ("PUT", "/api/email-config"): ("E-Mail", "Einstellungen geändert"),
    ("POST", "/api/file-restore/runs"): ("Datei-Restore", "gestartet"),
    ("POST", "/api/file-restore/runs/{run_id}/cleanup"): ("Datei-Restore", "beendet/aufgeräumt"),
    ("POST", "/api/file-restore/runs/{run_id}/copy"): ("Datei-Restore", "Dateien kopiert"),
    ("POST", "/api/hyperv/clusters"): ("Hyper-V-Cluster", "hinzugefügt"),
    ("DELETE", "/api/hyperv/clusters/{cluster_id}"): ("Hyper-V-Cluster", "entfernt"),
    ("PUT", "/api/hyperv/clusters/{cluster_id}"): ("Hyper-V-Cluster", "geändert"),
    ("POST", "/api/hyperv/clusters/{cluster_id}/discover"): ("Hyper-V-Cluster", "Discovery gestartet"),
    ("POST", "/api/jobs"): ("Policy", "angelegt"),
    ("DELETE", "/api/jobs/backups/{snapshot_id}"): ("Backup", "gelöscht"),
    ("POST", "/api/jobs/backups/{snapshot_id}/detach-vm"): ("Backup", "VM aus Backup gelöst"),
    ("DELETE", "/api/jobs/{job_id}"): ("Policy", "gelöscht"),
    ("PUT", "/api/jobs/{job_id}"): ("Policy", "geändert"),
    ("POST", "/api/jobs/{job_id}/pause"): ("Policy", "pausiert"),
    ("POST", "/api/jobs/{job_id}/resume"): ("Policy", "fortgesetzt"),
    ("POST", "/api/jobs/{job_id}/run"): ("Backup", "manuell gestartet"),
    ("POST", "/api/jobs/runs/{run_id}/cancel"): ("Backup", "abgebrochen"),
    ("PUT", "/api/kerberos-config"): ("Kerberos", "Einstellungen geändert"),
    ("POST", "/api/netapp/clusters"): ("NetApp-System", "hinzugefügt"),
    ("DELETE", "/api/netapp/clusters/{cluster_id}"): ("NetApp-System", "entfernt"),
    ("PUT", "/api/netapp/clusters/{cluster_id}"): ("NetApp-System", "geändert"),
    ("POST", "/api/netapp/clusters/{cluster_id}/cluster-peers"): ("Cluster Peer", "angelegt"),
    ("POST", "/api/netapp/clusters/{cluster_id}/discover"): ("NetApp-System", "Discovery gestartet"),
    ("POST", "/api/netapp/clusters/{cluster_id}/enroll-certificate"): ("NetApp-System", "Zertifikat eingerichtet"),
    ("POST", "/api/netapp/clusters/{cluster_id}/igroups"): ("IGroup", "angelegt"),
    ("POST", "/api/netapp/clusters/{cluster_id}/lun-maps"): ("LUN", "gemappt"),
    ("DELETE", "/api/netapp/clusters/{cluster_id}/lun-maps/{lun_uuid}"): ("LUN", "Mapping entfernt"),
    ("POST", "/api/netapp/clusters/{cluster_id}/luns"): ("LUN", "angelegt"),
    ("DELETE", "/api/netapp/clusters/{cluster_id}/luns/{lun_uuid}"): ("LUN", "gelöscht"),
    ("PATCH", "/api/netapp/clusters/{cluster_id}/luns/{lun_uuid}"): ("LUN", "geändert"),
    ("POST", "/api/netapp/clusters/{cluster_id}/schedules"): ("ONTAP-Schedule", "angelegt"),
    ("POST", "/api/netapp/clusters/{cluster_id}/snapmirror-policies"): ("SnapMirror-Policy", "angelegt"),
    ("PATCH", "/api/netapp/clusters/{cluster_id}/snapmirror-policies/{policy_uuid}"): ("SnapMirror-Policy", "geändert"),
    ("POST", "/api/netapp/clusters/{cluster_id}/snapmirror-relationships"): ("SnapMirror", "Beziehung angelegt"),
    ("PATCH", "/api/netapp/clusters/{cluster_id}/snapmirror-relationships/{relationship_uuid}"): ("SnapMirror", "Beziehung geändert"),
    ("POST", "/api/netapp/clusters/{cluster_id}/snapmirror-relationships/{relationship_uuid}/initialize"): ("SnapMirror", "initialisiert"),
    ("POST", "/api/netapp/clusters/{cluster_id}/snapmirror-relationships/{relationship_uuid}/update"): ("SnapMirror", "Update ausgelöst"),
    ("POST", "/api/netapp/clusters/{cluster_id}/svm-peers"): ("SVM Peer", "angelegt"),
    ("POST", "/api/netapp/clusters/{cluster_id}/volumes"): ("Volume", "angelegt"),
    ("DELETE", "/api/netapp/clusters/{cluster_id}/volumes/{volume_uuid}"): ("Volume", "gelöscht"),
    ("PATCH", "/api/netapp/clusters/{cluster_id}/volumes/{volume_uuid}"): ("Volume", "geändert"),
    ("DELETE", "/api/netapp/clusters/{cluster_id}/volumes/{volume_uuid}/snapshots/{snapshot_uuid}"): ("Snapshot", "gelöscht"),
    ("POST", "/api/protection-classes"): ("Schutzklasse", "angelegt"),
    ("PUT", "/api/protection-classes/assignments"): ("Schutzklasse", "Zuordnung geändert"),
    ("PUT", "/api/protection-classes/{class_id}"): ("Schutzklasse", "geändert"),
    ("DELETE", "/api/protection-classes/{class_id}"): ("Schutzklasse", "gelöscht"),
    ("POST", "/api/reports/definitions"): ("Report-Vorlage", "angelegt"),
    ("DELETE", "/api/reports/definitions/{definition_id}"): ("Report-Vorlage", "gelöscht"),
    ("PUT", "/api/reports/definitions/{definition_id}"): ("Report-Vorlage", "geändert"),
    ("POST", "/api/reports/definitions/{definition_id}/run"): ("Report", "aus Vorlage erstellt"),
    ("POST", "/api/reports/generate"): ("Report", "erstellt"),
    ("DELETE", "/api/reports/runs/{run_id}"): ("Report", "aus Historie gelöscht"),
    ("POST", "/api/resource-groups"): ("Protection Group", "angelegt"),
    ("DELETE", "/api/resource-groups/{group_id}"): ("Protection Group", "gelöscht"),
    ("PUT", "/api/resource-groups/{group_id}"): ("Protection Group", "geändert"),
    ("POST", "/api/resource-groups/{group_id}/pause"): ("Protection Group", "pausiert"),
    ("POST", "/api/resource-groups/{group_id}/resume"): ("Protection Group", "fortgesetzt"),
    ("POST", "/api/restore-infra/clusters/{cluster_id}/lif"): ("Restore-Setup", "iSCSI-LIF angelegt"),
    ("POST", "/api/restore-infra/clusters/{cluster_id}/setup"): ("Restore-Setup", "eingerichtet"),
    ("DELETE", "/api/restore-infra/configs/{config_id}"): ("Restore-Setup", "entfernt"),
    ("PUT", "/api/restore-infra/proxy-host"): ("Restore-Setup", "Proxy-Host geändert"),
    ("POST", "/api/restore/runs"): ("Restore", "Disk-Restore gestartet"),
    ("POST", "/api/restore/runs/{run_id}/cleanup"): ("Restore", "aufgeräumt"),
    ("POST", "/api/restore/vms/{vm_name}/recreate"): ("Restore", "VM-Neuerstellung gestartet"),
    ("PUT", "/api/scheduler-config"): ("Hintergrundjobs", "Einstellungen geändert"),
    ("POST", "/api/scheduler-config/run-snapshot-reconciliation"): ("Hintergrundjobs", "Snapshot-Abgleich gestartet"),
    ("POST", "/api/schedules"): ("Zeitplan", "angelegt"),
    ("DELETE", "/api/schedules/{schedule_id}"): ("Zeitplan", "gelöscht"),
    ("PUT", "/api/schedules/{schedule_id}"): ("Zeitplan", "geändert"),
    ("POST", "/api/schedules/{schedule_id}/pause"): ("Zeitplan", "pausiert"),
    ("POST", "/api/schedules/{schedule_id}/resume"): ("Zeitplan", "fortgesetzt"),
    ("POST", "/api/sites"): ("Standort", "angelegt"),
    ("PUT", "/api/sites/assignments/csv"): ("Standort", "CSV-Zuordnung geändert"),
    ("PUT", "/api/sites/assignments/netapp"): ("Standort", "NetApp-Zuordnung geändert"),
    ("PUT", "/api/sites/assignments/node"): ("Standort", "Knoten-Zuordnung geändert"),
    ("DELETE", "/api/sites/{site_id}"): ("Standort", "gelöscht"),
    ("PUT", "/api/sites/{site_id}"): ("Standort", "geändert"),
    ("POST", "/api/smb-create"): ("SMB3-Freigabe", "Anlage gestartet"),
    ("POST", "/api/smb-create/runs/{run_id}/keep"): ("SMB3-Freigabe", "Teilergebnis behalten"),
    ("POST", "/api/smb-create/runs/{run_id}/rollback"): ("SMB3-Freigabe", "Anlage zurückgerollt"),
    ("POST", "/api/smb-delete"): ("SMB3-Freigabe", "Löschen gestartet"),
    ("POST", "/api/smb-resize"): ("SMB3-Freigabe", "vergrößert"),
    ("POST", "/api/snapmirror-labels"): ("SnapMirror-Label", "angelegt"),
    ("DELETE", "/api/snapmirror-labels/{label_id}"): ("SnapMirror-Label", "gelöscht"),
    ("PUT", "/api/snapmirror-labels/{label_id}"): ("SnapMirror-Label", "geändert"),
    ("PUT", "/api/storage-access"): ("Storage-Zugriff", "Einstellungen geändert"),
    ("POST", "/api/users"): ("Benutzer", "angelegt"),
    ("POST", "/api/users/ad-add"): ("Benutzer", "aus AD hinzugefügt"),
    ("DELETE", "/api/users/{user_id}"): ("Benutzer", "gelöscht"),
    ("PUT", "/api/users/{user_id}/password"): ("Benutzer", "Passwort geändert"),
    ("PUT", "/api/users/{user_id}/role"): ("Benutzer", "Rolle geändert"),
    ("POST", "/api/vm-create"): ("VM", "Anlage gestartet"),
    ("POST", "/api/vm-create/runs/{run_id}/keep"): ("VM", "Teilergebnis behalten"),
    ("POST", "/api/vm-create/runs/{run_id}/rollback"): ("VM", "Anlage zurückgerollt"),
    ("POST", "/api/vm-delete"): ("VM", "Löschen gestartet"),
    ("POST", "/api/vm-settings"): ("VM", "Einstellungen geändert"),
    ("POST", "/api/vm-moves"): ("VM", "Live-Migration gestartet"),
    ("POST", "/api/vm-moves/{run_id}/cancel"): ("VM", "Verschieben abgebrochen"),
    ("POST", "/api/vm-moves/storage"): ("VM", "Storage-Verschiebung gestartet"),
    ("POST", "/api/vm-power"): ("VM", "Power-Aktion"),
    ("POST", "/api/vms/{cluster_id}/{vm_name}/checkpoints/{checkpoint_id}/delete"): ("VM", "Checkpoint gelöscht"),
    ("POST", "/api/vms/{cluster_id}/{vm_name}/discover"): ("VM", "Discovery gestartet"),
    ("POST", "/api/winrm-certs"): ("WinRM-Zertifikat", "hochgeladen"),
    ("DELETE", "/api/winrm-certs/{cert_id}"): ("WinRM-Zertifikat", "gelöscht"),
}

_DEFAULT_ACTION = {"POST": "ausgeführt", "PUT": "geändert", "PATCH": "geändert", "DELETE": "gelöscht"}

# Namensfelder im JSON-Body, die als Ziel taugen (erste vorhandene gewinnt).
BODY_NAME_KEYS = (
    "vm_name", "target_vm_name", "csv_name", "share_name", "name", "username", "display_name", "volume_name", "lun_name",
)


def _label(method: str, template: str) -> tuple[str, str]:
    if (method, template) in ROUTES:
        return ROUTES[(method, template)]
    # unbekannter Endpunkt: erster Pfadteil als Bereich
    parts = [p for p in template.removeprefix("/api/").split("/") if p and not p.startswith("{")]
    return (parts[0] if parts else template), _DEFAULT_ACTION.get(method, method)


def _compile(template: str) -> re.Pattern:
    pattern = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(template))
    return re.compile(f"^{pattern}$")


# Statische Pfade vor solchen mit Platzhaltern (z.B. /sites/assignments/csv vor /sites/{site_id}).
_TEMPLATES = sorted(
    ((method, template, _compile(template)) for method, template in set(ROUTES) | SKIP),
    key=lambda t: t[1].count("{"),
)


def _match(method: str, path: str) -> tuple[str, dict]:
    """(Pfad-Vorlage, Platzhalter) -- ohne Treffer die Rohdaten des Pfads.
    Bewusst eigener Abgleich statt FastAPIs Routing (dessen interne Router-
    Struktur sich zwischen Versionen aendert)."""
    for m, template, regex in _TEMPLATES:
        if m == method:
            hit = regex.match(path)
            if hit:
                return template, hit.groupdict()
    return path, {}


def _resolve_target(db, template: str, params: dict) -> str | None:
    """Name des Objekts zu einer ID im Pfad (vor der Ausfuehrung, damit auch
    Geloeschtes benannt bleibt)."""
    from app.api.routes.activities import KINDS
    from app.models.backup_policy import BackupPolicy
    from app.models.backup_run import BackupRunSnapshot
    from app.models.hyperv_cluster import HyperVCluster
    from app.models.netapp_cluster import NetAppCluster
    from app.models.netapp_discovery import NetAppLun, NetAppSnapMirrorPolicy, NetAppSnapMirrorRelationship, NetAppVolume
    from app.models.protection_class import ProtectionClass
    from app.models.report import ReportDefinition, ReportRun
    from app.models.resource_group import ResourceGroup
    from app.models.schedule import Schedule
    from app.models.site import Site
    from app.models.snapmirror_label import SnapMirrorLabel
    from app.models.user import User
    from app.models.winrm_cert import WinrmHostCertificate

    def by_id(model, attr, value):
        obj = db.get(model, value)
        return getattr(obj, attr, None) if obj is not None else None

    def by_uuid(model, attr, value):
        obj = db.query(model).filter(model.uuid == value).first()
        return getattr(obj, attr, None) if obj is not None else None

    names: list[str] = []
    for key, value in params.items():
        name = None
        if key == "vm_name":
            name = value
        elif key == "job_id":
            name = by_id(BackupPolicy, "name", value)
        elif key == "group_id":
            name = by_id(ResourceGroup, "name", value)
        elif key == "schedule_id":
            name = by_id(Schedule, "name", value)
        elif key == "site_id":
            name = by_id(Site, "name", value)
        elif key == "label_id":
            name = by_id(SnapMirrorLabel, "name", value)
        elif key == "class_id":
            name = by_id(ProtectionClass, "name", value)
        elif key == "definition_id":
            name = by_id(ReportDefinition, "name", value)
        elif key == "user_id":
            name = by_id(User, "username", value)
        elif key == "cert_id":
            name = by_id(WinrmHostCertificate, "label", value)
        elif key == "cluster_id":
            # Bei Unterobjekten (Volume, LUN, ...) reicht deren Name.
            if len(params) == 1 and not template.startswith("/api/vms/"):  # dort genuegt der VM-Name
                model = HyperVCluster if template.startswith("/api/hyperv/") else NetAppCluster
                name = by_id(model, "name", value)
        elif key == "volume_uuid":
            name = by_uuid(NetAppVolume, "name", value)
        elif key == "lun_uuid":
            name = by_uuid(NetAppLun, "name", value)
        elif key == "relationship_uuid":
            name = by_uuid(NetAppSnapMirrorRelationship, "destination_path", value)
        elif key == "policy_uuid":
            name = by_uuid(NetAppSnapMirrorPolicy, "name", value)
        elif key == "snapshot_id":
            snap = db.get(BackupRunSnapshot, value)
            name = f"{snap.volume_name}: {snap.snapshot_name}" if snap else None
        elif key == "snapshot_uuid":
            name = None  # Volume-Name steht schon drin
        elif key == "run_id":
            prefixes = {
                "/api/csv-create/": "csv_create", "/api/smb-create/": "smb_create", "/api/vm-create/": "vm_create",
                "/api/vm-moves/": "vm_move", "/api/restore/runs/": "restore", "/api/file-restore/": "file_restore",
                "/api/jobs/runs/": "backup", "/api/alerts/backup-runs/": "backup",
            }
            kind = next((KINDS[k] for p, k in prefixes.items() if template.startswith(p)), None)
            if kind is not None:
                run = db.get(kind.model, value)
                name = kind.target(run) if run is not None else None
            elif template.startswith("/api/reports/runs/"):
                name = by_id(ReportRun, "title", value)
        if name:
            names.append(str(name))
    return " / ".join(names) or None


def _body_target(body: bytes) -> str | None:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in BODY_NAME_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            extra = data.get("action")
            return f"{value} ({extra})" if key == "vm_name" and isinstance(extra, str) else value
    return None


def _prepare(token: str | None, template: str, params: dict) -> tuple[str | None, str | None]:
    """(Benutzername, Ziel aus dem Pfad) -- synchron, im Threadpool."""
    from app.db.session import SessionLocal
    from app.models.user import User

    payload = decode_access_token(token) if token else None
    if not payload or "sub" not in payload:
        return None, None
    db = SessionLocal()
    try:
        user = db.get(User, payload["sub"])
        if user is None:
            return None, None
        try:
            target = _resolve_target(db, template, params)
        except Exception:  # noqa: BLE001
            target = None
        return (user.display_name or user.username), target
    finally:
        db.close()


def _store(**fields) -> None:
    from app.db.session import SessionLocal
    from app.models.audit import AuditEvent

    db = SessionLocal()
    try:
        db.add(AuditEvent(**fields))
        db.commit()
    finally:
        db.close()


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in MUTATING or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        template, params = _match(method, scope["path"])
        if (method, template) in SKIP:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        try:
            username, target = await anyio.to_thread.run_sync(_prepare, token, template, params)
        except Exception:  # noqa: BLE001
            log.exception("Audit: Vorbereitung fehlgeschlagen")
            username, target = None, None
        if username is None:
            # nicht angemeldet -> die Anfrage scheitert ohnehin mit 401
            await self.app(scope, receive, send)
            return

        # JSON-Body einmal lesen (fuer den Zielnamen) und unveraendert weiterreichen.
        replay = receive
        if "application/json" in headers.get("content-type", ""):
            messages, size = [], 0
            while True:
                message = await receive()
                messages.append(message)
                size += len(message.get("body", b""))
                if message["type"] != "http.request" or not message.get("more_body") or size > MAX_BODY:
                    break
            body_name = _body_target(b"".join(m.get("body", b"") for m in messages)) if size <= MAX_BODY else None
            # Pfad-Ziel (z.B. NetApp-System) + Name aus dem Body (z.B. neues Volume)
            if body_name and body_name != target:
                target = f"{body_name} ({target})" if target else body_name
            pending = list(messages)

            async def _replay():
                return pending.pop(0) if pending else await receive()

            replay = _replay

        status_holder = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, replay, send_wrapper)
        finally:
            area, action = _label(method, template)
            client = headers.get("x-forwarded-for", "").split(",")[0].strip() or (scope.get("client") or ("",))[0]
            try:
                await anyio.to_thread.run_sync(lambda: _store(
                    username=username, area=area, action=action, target=(target or "")[:500] or None, method=method,
                    path=scope["path"][:1000], status_code=status_holder["code"], client_ip=client or None,
                ))
            except Exception:  # noqa: BLE001
                log.exception("Audit: Eintrag konnte nicht gespeichert werden")
