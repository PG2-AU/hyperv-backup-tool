"""VMs starten und herunterfahren (Nutzer-Anfrage 2026-10-02): Aktionen in
Inventory > VMs -- Starten, Herunterfahren (ueber das Gastbetriebssystem) und
Ausschalten (hart). Laeuft als Hintergrund-Task, weil ein Herunterfahren
Minuten dauern kann; der Stand je VM liegt nur im Speicher (kein
Schritt-Protokoll noetig, ein Neustart der App vergisst hoechstens die
Anzeige einer laufenden Aktion), Ergebnis zusaetzlich im System-Log.

Gesperrt, solange ein Backup die VM gerade sichert oder ein Restore bzw. eine
Verschiebung der VM laeuft."""

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import SessionLocal, get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVVm
from app.models.restore_run import RestoreRun, RestoreStatus
from app.models.system_log import SystemLogEvent
from app.models.vm_move_run import VmMoveRun
from app.services.hyperv_service import HyperVService

router = APIRouter(prefix="/api/vm-power", tags=["vm-power"])

PowerActionName = Literal["start", "shutdown", "turn_off"]
_LABEL = {"start": "Starten", "shutdown": "Herunterfahren", "turn_off": "Ausschalten"}
# Fertige Aktionen bleiben so lange abrufbar (fuer die Rueckmeldung im Browser).
_KEEP = timedelta(minutes=15)


class PowerRequest(BaseModel):
    cluster_id: str
    vm_name: str
    action: PowerActionName


class PowerAction(BaseModel):
    id: str
    cluster_id: str
    vm_name: str
    action: PowerActionName
    status: Literal["running", "succeeded", "failed"] = "running"
    state_after: str | None = None
    error_message: str | None = None
    requested_by: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


_lock = threading.Lock()
_actions: dict[tuple[str, str], PowerAction] = {}


def _prune() -> None:
    cutoff = datetime.now(timezone.utc) - _KEEP
    for key in [k for k, a in _actions.items() if a.finished_at and a.finished_at < cutoff]:
        del _actions[key]


def _busy_reason(db: Session, cluster_id: str, vm_name: str) -> str | None:
    for run in db.query(BackupRun).filter(BackupRun.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.CLEANING_UP])).all():
        if vm_name in (run.targets or []):
            return f"Backup-Lauf '{run.policy_name}' sichert diese VM gerade -- bitte das Ende abwarten."
    if db.query(RestoreRun).filter(
        RestoreRun.hyperv_cluster_id == cluster_id, RestoreRun.vm_name == vm_name, RestoreRun.status == RestoreStatus.RUNNING,
    ).first():
        return "Für diese VM läuft gerade ein Restore."
    if db.query(VmMoveRun).filter(
        VmMoveRun.hyperv_cluster_id == cluster_id, VmMoveRun.vm_name == vm_name, VmMoveRun.status == RestoreStatus.RUNNING,
    ).first():
        return "Diese VM wird gerade verschoben."
    return None


@router.get("", response_model=list[PowerAction])
def list_actions(user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[PowerAction]:
    with _lock:
        _prune()
        return list(_actions.values())


@router.post("", response_model=PowerAction, status_code=status.HTTP_202_ACCEPTED)
def start_action(
    payload: PowerRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> PowerAction:
    vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == payload.cluster_id, HyperVVm.name == payload.vm_name).first()
    if vm is None or db.get(HyperVCluster, payload.cluster_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM nicht gefunden")
    busy = _busy_reason(db, payload.cluster_id, payload.vm_name)
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    key = (payload.cluster_id, payload.vm_name)
    with _lock:
        _prune()
        current = _actions.get(key)
        if current is not None and current.status == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Für diese VM läuft bereits '{_LABEL[current.action]}'.",
            )
        action = PowerAction(
            id=str(uuid.uuid4()), cluster_id=payload.cluster_id, vm_name=payload.vm_name, action=payload.action,
            requested_by=user.display_name or user.username, started_at=datetime.now(timezone.utc),
        )
        _actions[key] = action
    background_tasks.add_task(_execute, action.id, key)
    return action


def _finish(key: tuple[str, str], action_id: str, **changes) -> None:
    with _lock:
        current = _actions.get(key)
        if current is not None and current.id == action_id:
            _actions[key] = current.model_copy(update={**changes, "finished_at": datetime.now(timezone.utc)})


def _execute(action_id: str, key: tuple[str, str]) -> None:
    cluster_id, vm_name = key
    with _lock:
        action = _actions.get(key)
    if action is None or action.id != action_id:
        return
    label = _LABEL[action.action]
    db = SessionLocal()
    try:
        try:
            cluster = db.get(HyperVCluster, cluster_id)
            vm = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
            if cluster is None or vm is None:
                raise RuntimeError("VM oder Cluster nicht mehr vorhanden")
            host_fallback = vm.host_name
            settings = get_settings()
            password = decrypt_secret(cluster.encrypted_password)
            cno = HyperVService(
                settings, cluster.management_address, use_https=cluster.use_https, node_hostname=cluster.hyperv_cluster_name,
            )
            cno_session = cno.connect(cluster.username, password, read_timeout_sec=30, operation_timeout_sec=20)
            owner = cno.get_vm_owner_node(cno_session, vm_name) or host_fallback
            if not owner:
                raise RuntimeError("Der Knoten der VM ist nicht bekannt -- bitte Discovery ausführen")
            node = HyperVService(
                settings, cno.resolve_node_address(cno_session, owner), use_https=cluster.use_https, node_hostname=owner,
            )
            # Herunterfahren wartet auf das Gastbetriebssystem (bis zu 5 Minuten).
            session = node.connect(cluster.username, password, read_timeout_sec=480, operation_timeout_sec=420)
            state = node.power_vm(session, vm_name, action.action)
            # Zeile erst jetzt frisch laden, siehe [[backup-vs-discovery-orm-race]].
            fresh = db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id, HyperVVm.name == vm_name).first()
            if fresh is not None and state:
                fresh.state = state
                fresh.host_name = owner
            db.add(SystemLogEvent(
                level="INFO", source="vm-power",
                message=f"VM '{vm_name}': {label} ausgeführt auf {owner}, Status jetzt {state or '?'} (durch {action.requested_by})",
            ))
            db.commit()
            _finish(key, action_id, status="succeeded", state_after=state or None)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            message = str(exc)[:1000]
            db.add(SystemLogEvent(
                level="ERROR", source="vm-power",
                message=f"VM '{vm_name}': {label} fehlgeschlagen: {message} (durch {action.requested_by})",
            ))
            db.commit()
            _finish(key, action_id, status="failed", error_message=message)
    finally:
        db.close()
