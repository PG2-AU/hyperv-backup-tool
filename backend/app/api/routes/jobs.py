"""Backup-Policies (frueher Job-Definitionen) und Job-Laeufe.

Backup-Policies (Name, Zeitplan, Konsistenz, SnapMirror-Verhalten, Retention,
Snapshot Locking) werden in der DB persistiert. Die VM/CSV-Zuordnung erfolgt
ueber ResourceGroups, die mit einer Policy verknuepft werden (siehe
app.api.routes.resource_groups).

Job-Ausfuehrung (aktueller Stand -- nur crashconsistent): pro betroffenem
NetApp-Volume wird genau EIN Storage-Snapshot erstellt, auch wenn mehrere
VMs/CSVs auf demselben Volume liegen. Die Aufloesung VM/CSV -> CSV -> LUN ->
NetApp-Volume nutzt die bereits bei der Hyper-V-/NetApp-Discovery persistierte
Korrelation (HyperVCsv.netapp_lun_id, siehe hyperv_clusters.py discover_cluster).
Jeder Snapshot-Vorgang wird als eigener BackupRunSnapshot-Datensatz mit der
vollstaendigen Zuordnungskette gespeichert.

Applikationskonsistente Backups (VM-Checkpoint vor dem Storage-Snapshot,
inkl. Best-Effort-Ueberspringen einzelner VMs und Checkpoint-Cleanup bei
Fehlern) folgen als naechster Schritt.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy, BackupScope, ConsistencyType
from app.models.backup_run import BackupRun, BackupRunSnapshot, JobStatus
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster
from app.models.netapp_discovery import NetAppLun, NetAppVolume
from app.models.schedule import Schedule
from app.models.snapmirror_label import SnapMirrorLabel
from app.schemas.backup import BackupJobRun, BackupPolicyRead, BackupPolicyWrite, BackupSnapshotRead
from app.services.netapp_service import NetAppOntapService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _validate_references(payload: BackupPolicyWrite, db: Session) -> None:
    if payload.schedule_id is not None and db.get(Schedule, payload.schedule_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zeitplan nicht gefunden")
    if payload.snapmirror_label_id is not None and db.get(SnapMirrorLabel, payload.snapmirror_label_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SnapMirror-Label nicht gefunden")


@router.get("", response_model=list[BackupPolicyRead])
def list_jobs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupPolicy]:
    return db.query(BackupPolicy).order_by(BackupPolicy.name).all()


@router.post("", response_model=BackupPolicyRead, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: BackupPolicyWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> BackupPolicy:
    if db.query(BackupPolicy).filter(BackupPolicy.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Policy mit diesem Namen existiert bereits")

    _validate_references(payload, db)

    policy = BackupPolicy(
        name=payload.name,
        schedule_id=payload.schedule_id,
        consistency=ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT,
        snapmirror_update=payload.snapmirror_update,
        snapmirror_label_id=payload.snapmirror_label_id,
        retention_type=payload.retention_type,
        retention_value=payload.retention_value,
        snapshot_locking_enabled=payload.snapshot_locking_enabled,
        snapshot_locking_days=payload.snapshot_locking_days,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.put("/{job_id}", response_model=BackupPolicyRead)
def update_job(
    job_id: str,
    payload: BackupPolicyWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> BackupPolicy:
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")

    duplicate = db.query(BackupPolicy).filter(BackupPolicy.name == payload.name, BackupPolicy.id != job_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Policy mit diesem Namen existiert bereits")

    _validate_references(payload, db)

    policy.name = payload.name
    policy.schedule_id = payload.schedule_id
    policy.consistency = ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT
    policy.snapmirror_update = payload.snapmirror_update
    policy.snapmirror_label_id = payload.snapmirror_label_id
    policy.retention_type = payload.retention_type
    policy.retention_value = payload.retention_value
    policy.snapshot_locking_enabled = payload.snapshot_locking_enabled
    policy.snapshot_locking_days = payload.snapshot_locking_days
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")
    db.delete(policy)
    db.commit()


@router.get("/runs", response_model=list[BackupJobRun])
def list_job_runs(
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[BackupJobRun]:
    query = db.query(BackupRun)
    if status_filter is not None:
        query = query.filter(BackupRun.status == status_filter)
    runs = query.order_by(BackupRun.started_at.desc()).all()
    return [_to_run_read(r) for r in runs]


def _to_run_read(run: BackupRun) -> BackupJobRun:
    return BackupJobRun(
        id=run.id,
        job_id=run.policy_id,
        job_name=run.policy_name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        scope=run.scope,
        targets=run.targets or [],
        error_message=run.error_message,
        snapshots=list(run.snapshots),
    )


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "policy"


@dataclass
class _VolumeTarget:
    netapp_cluster_id: str
    netapp_cluster_name: str | None
    svm_name: str | None
    volume_name: str | None
    csv_names: set[str] = field(default_factory=set)
    lun_names: set[str] = field(default_factory=set)
    vm_names: set[str] = field(default_factory=set)


def _csv_index(db: Session) -> tuple[dict[str, HyperVCsv], dict[str, NetAppLun], dict[str, str]]:
    """csv_by_name, luns_by_serial, cluster_names_by_id.

    luns_by_serial ist bewusst ueber die (stabile) Disk-Seriennummer indiziert
    und NICHT ueber die bei der Hyper-V-Discovery gespeicherte
    HyperVCsv.netapp_lun_id: NetAppLun.id ist eine bei JEDER NetApp-Discovery
    neu vergebene UUID (delete-then-reinsert), wird ein NetApp-Cluster also
    unabhaengig vom Hyper-V-Cluster neu discovert, waere die gespeicherte
    netapp_lun_id sofort veraltet und jede Backup-Ausfuehrung wuerde
    faelschlich "LUN nicht gefunden" melden. Die Seriennummer bleibt dagegen
    ueber Rediscoveries hinweg stabil (echte Hardware-Eigenschaft der LUN)."""
    csv_by_name = {c.name: c for c in db.query(HyperVCsv).all()}
    luns_by_serial = {lun.serial_number: lun for lun in db.query(NetAppLun).all() if lun.serial_number}
    cluster_names_by_id = {c.id: c.ontap_cluster_name or c.name for c in db.query(NetAppCluster).all()}
    return csv_by_name, luns_by_serial, cluster_names_by_id


def _vhd_maps(db: Session) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """VM-Name -> Menge der CSV-Namen, auf denen seine VHDs liegen, und
    umgekehrt CSV-Name -> Menge der VM-Namen darauf."""
    vm_csvs: dict[str, set[str]] = defaultdict(set)
    csv_vms: dict[str, set[str]] = defaultdict(set)
    for vhd in db.query(HyperVVhd).all():
        if vhd.vm_name and vhd.csv_name:
            vm_csvs[vhd.vm_name].add(vhd.csv_name)
            csv_vms[vhd.csv_name].add(vhd.vm_name)
    return vm_csvs, csv_vms


def _csv_volume_key(csv: HyperVCsv, luns_by_serial: dict[str, NetAppLun]) -> tuple[str, str, str] | None:
    """Loest ein CSV LIVE (nicht ueber gespeicherte IDs) ueber seine
    Disk-Seriennummer zu seinem aktuellen NetApp-Volume auf
    (cluster_id, svm_name, volume_name). Siehe _csv_index."""
    if not csv.disk_serial_number:
        return None
    lun = luns_by_serial.get(csv.disk_serial_number)
    if lun is None or not lun.volume_name:
        return None
    return (lun.cluster_id, lun.svm_name or "", lun.volume_name)


def _resolve_targets(db: Session, policy: BackupPolicy) -> tuple[list[_VolumeTarget], list[str]]:
    """Loest die Resource Groups einer Policy zu den betroffenen NetApp-Volumes
    auf. Mehrere VMs/CSVs auf demselben Volume werden zu einem gemeinsamen
    Ziel zusammengefasst (ein Snapshot deckt sie alle ab). Gibt zusaetzlich
    Warnungen fuer Mitglieder zurueck, die sich keinem NetApp-Volume zuordnen
    liessen (z.B. weil die Discovery noch nicht gelaufen ist)."""
    warnings: list[str] = []
    targets: dict[tuple[str, str, str], _VolumeTarget] = {}

    csv_by_name, luns_by_serial, cluster_names_by_id = _csv_index(db)
    vm_csvs, csv_vms = _vhd_maps(db)

    def _add(csv_name: str, vm_names: set[str]) -> None:
        csv = csv_by_name.get(csv_name)
        if csv is None:
            warnings.append(f"CSV '{csv_name}' nicht gefunden (Hyper-V-Discovery pruefen)")
            return
        if not csv.disk_serial_number:
            warnings.append(f"CSV '{csv_name}': keine Disk-Seriennummer ermittelt (Hyper-V-Discovery pruefen)")
            return
        lun = luns_by_serial.get(csv.disk_serial_number)
        if lun is None or not lun.volume_name:
            warnings.append(
                f"CSV '{csv_name}': keine passende NetApp-LUN gefunden (Seriennummer {csv.disk_serial_number}) -- "
                "NetApp-Cluster erneut discovern?"
            )
            return
        key = (lun.cluster_id, lun.svm_name or "", lun.volume_name)

        target = targets.get(key)
        if target is None:
            target = _VolumeTarget(
                netapp_cluster_id=key[0],
                netapp_cluster_name=cluster_names_by_id.get(lun.cluster_id),
                svm_name=lun.svm_name,
                volume_name=lun.volume_name,
            )
            targets[key] = target
        target.csv_names.add(csv_name)
        if lun.name:
            target.lun_names.add(lun.name)
        target.vm_names |= vm_names

    for group in policy.resource_groups:
        if group.scope == BackupScope.VM:
            for vm_name in group.members:
                csvs = vm_csvs.get(vm_name)
                if not csvs:
                    warnings.append(f"VM '{vm_name}': keine CSV/VHD-Zuordnung gefunden (Hyper-V-Discovery pruefen)")
                    continue
                for csv_name in csvs:
                    _add(csv_name, {vm_name})
        elif group.scope == BackupScope.CSV:
            for csv_name in group.members:
                _add(csv_name, csv_vms.get(csv_name, set()))
        else:
            warnings.append(f"Resource Group '{group.name}': Scope '{group.scope}' wird fuer Backup-Jobs nicht unterstuetzt")

    return list(targets.values()), warnings


def _netapp_service_for(cluster: NetAppCluster) -> NetAppOntapService:
    if cluster.auth_method == NetAppAuthMethod.CERTIFICATE and cluster.client_cert_path and cluster.client_key_path:
        return NetAppOntapService(
            host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
            cert_path=cluster.client_cert_path, key_path=cluster.client_key_path,
        )
    return NetAppOntapService(
        host=cluster.management_lif, verify_ssl=cluster.verify_ssl,
        username=cluster.username,
        password=decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else None,
    )


def _resolve_volume_keys_for_object(db: Session, scope: BackupScope, name: str) -> list[tuple[str, str, str]]:
    """Loest eine einzelne VM oder ein einzelnes CSV (aktueller Discovery-
    Stand) zu den NetApp-Volumes auf, auf denen ihre Daten liegen -- fuer die
    'vorhandene Backups anzeigen'-Funktion im Inventory, unabhaengig von
    Resource Groups/Policies."""
    csv_by_name, luns_by_serial, _ = _csv_index(db)

    csv_names: set[str] = set()
    if scope == BackupScope.VM:
        vm_csvs, _ = _vhd_maps(db)
        csv_names = vm_csvs.get(name, set())
    elif scope == BackupScope.CSV:
        csv_names = {name}

    keys: set[tuple[str, str, str]] = set()
    for csv_name in csv_names:
        csv = csv_by_name.get(csv_name)
        if csv is None:
            continue
        key = _csv_volume_key(csv, luns_by_serial)
        if key is not None:
            keys.add(key)
    return list(keys)


@router.get("/backups", response_model=list[BackupSnapshotRead])
def list_backups_for_object(
    scope: BackupScope, name: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[BackupSnapshotRead]:
    """Vorhandene (erfolgreiche) Snapshots, die eine bestimmte VM oder ein
    bestimmtes CSV abdecken -- unabhaengig davon, ueber welche Policy sie
    entstanden sind. Wird vom Inventory (Rechtsklick -> Backups anzeigen)
    verwendet."""
    keys = set(_resolve_volume_keys_for_object(db, scope, name))
    if not keys:
        return []

    rows = db.query(BackupRunSnapshot).filter(BackupRunSnapshot.success.is_(True)).all()
    matched = [r for r in rows if (r.netapp_cluster_id, r.svm_name or "", r.volume_name or "") in keys]
    if scope == BackupScope.VM:
        # Ein Snapshot deckt ggf. mehrere VMs ab (gemeinsames CSV/Volume) --
        # per detach-vm kann eine VM manuell aus vm_names entfernt werden
        # (siehe unten), ohne den Snapshot selbst anzutasten. Ohne diesen
        # Filter wuerde sie ihn trotzdem weiterhin sehen, da oben nur ueber
        # den Volume-Key gematcht wird.
        matched = [r for r in matched if name in (r.vm_names or [])]
    matched.sort(key=lambda r: r.created_at, reverse=True)

    return [
        BackupSnapshotRead(
            id=r.id,
            run_id=r.run_id,
            policy_name=r.run.policy_name,
            consistency=r.run.consistency,
            created_at=r.created_at,
            netapp_cluster_name=r.netapp_cluster_name,
            svm_name=r.svm_name,
            volume_name=r.volume_name,
            csv_names=r.csv_names or [],
            vm_names=r.vm_names or [],
            snapshot_name=r.snapshot_name,
            snapshot_uuid=r.snapshot_uuid,
        )
        for r in matched
    ]


@router.delete("/backups/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup_snapshot(
    snapshot_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    """Loescht einen Snapshot vollstaendig -- auf der NetApp *und* seinen
    BackupRunSnapshot-Datensatz (Hard-Delete, bewusst anders als der
    automatische Abgleich in app.core.scheduler, der bei unerwartet
    verschwundenen Snapshots nur success=False setzt: hier loest der Nutzer
    die Loeschung explizit selbst aus). Betrifft alle in vm_names gelisteten
    VMs -- die Bestaetigung dafuer erfolgt im Frontend."""
    row = db.get(BackupRunSnapshot, snapshot_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot nicht gefunden")
    if not row.volume_uuid or not row.snapshot_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Snapshot hat keine Volume-/Snapshot-UUID")

    cluster = db.get(NetAppCluster, row.netapp_cluster_id) if row.netapp_cluster_id else None
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="NetApp-Cluster des Snapshots nicht gefunden")

    service = _netapp_service_for(cluster)
    result = service.delete_snapshot(row.volume_uuid, row.snapshot_uuid)
    if not result.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Snapshot konnte nicht geloescht werden: {result.message}")

    db.delete(row)
    db.commit()


@router.post("/backups/{snapshot_id}/detach-vm", response_model=BackupSnapshotRead)
def detach_vm_from_backup_snapshot(
    snapshot_id: str, vm_name: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> BackupRunSnapshot:
    """Entfernt nur die Zuordnung einer VM zu diesem Snapshot aus der DB --
    der Snapshot selbst bleibt auf der NetApp und fuer andere VMs/das CSV
    unveraendert bestehen. Fuer den Fall, dass ein Snapshot mehrere VMs
    abdeckt (gemeinsames CSV/Volume) und nur eine davon aus der Historie
    verschwinden soll."""
    row = db.get(BackupRunSnapshot, snapshot_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot nicht gefunden")
    row.vm_names = [v for v in (row.vm_names or []) if v != vm_name]
    db.commit()
    db.refresh(row)
    return row


@router.post("/{job_id}/run", response_model=BackupJobRun)
def trigger_job_run(
    job_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_RUN)),
) -> BackupJobRun:
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")

    if policy.consistency != ConsistencyType.CRASH_CONSISTENT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Applikationskonsistente Backups (VM-Checkpoints) sind noch nicht implementiert.",
        )

    targets, warnings = _resolve_targets(db, policy)
    if not targets:
        detail = "Keine gueltigen Backup-Ziele gefunden."
        if warnings:
            detail += " " + "; ".join(warnings)
        else:
            detail += " Der Policy ist keine Resource Group mit Zielen zugeordnet."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    now = datetime.now(timezone.utc)
    all_targets = sorted({vm for t in targets for vm in t.vm_names} | {csv for t in targets for csv in t.csv_names})
    run = BackupRun(
        policy_id=policy.id,
        policy_name=policy.name,
        status=JobStatus.RUNNING,
        consistency=policy.consistency.value,
        scope=policy.resource_groups[0].scope if policy.resource_groups else None,
        targets=all_targets,
        started_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    # Sofort committen (statt erst am Ende), damit der Lauf mit Status
    # "running" fuer andere Sessions/Tabs sichtbar ist, waehrend die
    # eigentlichen Snapshot-Aufrufe noch laufen (siehe GET /jobs/runs?status=running,
    # von der Laufende-Jobs-Anzeige im Frontend gepollt).

    clusters_by_id = {c.id: c for c in db.query(NetAppCluster).all()}
    volumes_by_key = {(v.cluster_id, v.svm_name, v.name): v for v in db.query(NetAppVolume).all()}
    snapshot_suffix = now.strftime("%Y%m%d%H%M%S")
    slug = _slugify(policy.name)
    label = policy.snapmirror_label.name if policy.snapmirror_label else None

    errors: list[str] = list(warnings)
    for target in targets:
        row = BackupRunSnapshot(
            run_id=run.id,
            netapp_cluster_id=target.netapp_cluster_id,
            netapp_cluster_name=target.netapp_cluster_name,
            svm_name=target.svm_name,
            volume_name=target.volume_name,
            csv_names=sorted(target.csv_names),
            lun_names=sorted(target.lun_names),
            vm_names=sorted(target.vm_names),
        )
        cluster = clusters_by_id.get(target.netapp_cluster_id)
        volume = volumes_by_key.get((target.netapp_cluster_id, target.svm_name, target.volume_name))
        if volume is not None:
            row.volume_uuid = volume.uuid

        if cluster is None or not target.svm_name or not target.volume_name:
            row.success = False
            row.error_message = "NetApp-Cluster oder -Volume nicht auflösbar"
            errors.append(f"{target.volume_name or '?'}: {row.error_message}")
            db.add(row)
            continue

        snapshot_name = f"hvnb_{slug}_{snapshot_suffix}"
        try:
            service = _netapp_service_for(cluster)
            snap = service.create_snapshot(target.volume_name, target.svm_name, snapshot_name, snapmirror_label=label)
            row.snapshot_name = snap.name
            row.snapshot_uuid = snap.uuid
            row.success = True
        except Exception as exc:
            row.success = False
            row.error_message = str(exc)
            errors.append(f"{target.volume_name}: {exc}")
        db.add(row)

    run.finished_at = datetime.now(timezone.utc)
    run.status = JobStatus.FAILED if errors else JobStatus.SUCCEEDED
    run.error_message = "; ".join(errors) if errors else None
    db.commit()
    db.refresh(run)

    return _to_run_read(run)
