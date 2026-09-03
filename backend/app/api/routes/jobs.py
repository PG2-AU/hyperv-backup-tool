"""Backup-Policies (frueher Job-Definitionen) und Job-Laeufe.

Backup-Policies (Name, Zeitplan, Konsistenz, SnapMirror-Verhalten, Retention,
Snapshot Locking) werden in der DB persistiert. Die VM/CSV-Zuordnung erfolgt
ueber ResourceGroups, die mit einer Policy verknuepft werden (siehe
app.api.routes.resource_groups).

Job-Ausfuehrung: pro betroffenem NetApp-Volume wird genau EIN Storage-
Snapshot erstellt, auch wenn mehrere VMs/CSVs auf demselben Volume liegen.
Die Aufloesung VM/CSV -> CSV -> LUN -> NetApp-Volume nutzt die bereits bei
der Hyper-V-/NetApp-Discovery persistierte Korrelation
(HyperVCsv.netapp_lun_id, siehe hyperv_clusters.py discover_cluster). Jeder
Snapshot-Vorgang wird als eigener BackupRunSnapshot-Datensatz mit der
vollstaendigen Zuordnungskette gespeichert.

Applikationskonsistente Backups erzeugen zusaetzlich VORHER auf jeder
betroffenen VM einen Hyper-V-Production-Checkpoint (VSS-Quiesce) und
entfernen ihn NACH dem Storage-Snapshot wieder (Merge der dabei
entstandenen Differencing-Disk zurueck in die Basis-VHDX) -- die Basis-VHDX
selbst wird durch den Checkpoint eingefroren und enthaelt bereits den
applikationskonsistenten Stand, ein Restore braucht daher keinen
Delta-Merge (siehe Chat-Verlauf). Scheitert der Checkpoint fuer eine
einzelne VM, wird das als Fehler vermerkt, das Backup laeuft fuer diese VM
aber trotzdem (crash-konsistent) weiter, statt den gesamten Lauf
abzubrechen -- Best-Effort pro VM, analog zum Rest dieser Funktion."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from ntpath import basename as win_basename
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.db.session import SessionLocal, get_db
from app.models.backup_policy import BackupPolicy, BackupScope, ConsistencyType
from app.api.routes.restore import _StepCtx
from app.models.backup_run import BackupRun, BackupRunSnapshot, BackupRunStep, BackupRunVmConfig, JobStatus
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd, HyperVVm
from app.models.netapp_cluster import NetAppAuthMethod, NetAppCluster
from app.models.netapp_discovery import NetAppLun, NetAppSnapMirrorRelationship, NetAppVolume
from app.models.resource_group import ResourceGroupPolicyLink, parse_member_key, resolve_member_key
from app.models.restore_infra import RestoreInfraConfig
from app.models.restore_run import RestoreStepStatus
from app.models.schedule import Schedule, ScheduleType
from app.models.snapmirror_label import SnapMirrorLabel
from app.services.email_service import notify_backup_failure
from app.schemas.backup import (
    BackupJobRun,
    BackupPolicyRead,
    BackupPolicyWrite,
    BackupRunStepRead,
    BackupSnapshotDestinationRead,
    BackupSnapshotRead,
    BackupSnapshotVhdRead,
    UpcomingJobRead,
)
from app.services.hyperv_service import HyperVService
from app.services.netapp_service import NetAppOntapService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _validate_references(payload: BackupPolicyWrite, db: Session) -> None:
    if payload.snapmirror_label_id is not None and db.get(SnapMirrorLabel, payload.snapmirror_label_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SnapMirror-Label nicht gefunden")


@router.get("", response_model=list[BackupPolicyRead])
def list_jobs(db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW))) -> list[BackupPolicy]:
    return db.query(BackupPolicy).order_by(BackupPolicy.name).all()


def _occurrences_within(schedule: Schedule, start_local: datetime, end_local: datetime) -> list[datetime]:
    """Alle Vorkommen eines Zeitplans im Intervall (start_local, end_local],
    in derselben lokalen Zeitzone -- fuer die Dashboard-Vorschau der
    naechsten Laeufe (list_upcoming_jobs) UND fuer den Nachhol-Mechanismus in
    app.core.scheduler.run_scheduled_backups (dort start_local = letzter
    erfolgreicher Check, end_local = jetzt, statt eines vorausschauenden
    Fensters). Ein HOURLY-Zeitplan kann dabei mehrfach im selben Fenster
    vorkommen (z.B. 6x/Tag), WEEKLY/MONTHLY typischerweise hoechstens einmal.
    Iteriert Tag fuer Tag ueber die Spanne (plus einen Tag Puffer), prueft
    pro Kandidatentag die Wochentag-/Monatstag-Bedingung, dann jede
    konfigurierte Uhrzeit dieses Tages der Reihe nach."""
    times_sorted = sorted(schedule.times)
    occurrences: list[datetime] = []
    day_span = (end_local.date() - start_local.date()).days + 1
    for day_offset in range(day_span + 1):
        candidate_date = (start_local + timedelta(days=day_offset)).date()
        if candidate_date > end_local.date():
            break
        if schedule.schedule_type == ScheduleType.WEEKLY and (
            schedule.weekday is None or candidate_date.weekday() != schedule.weekday
        ):
            continue
        if schedule.schedule_type == ScheduleType.MONTHLY and (
            schedule.day_of_month is None or candidate_date.day != schedule.day_of_month
        ):
            continue
        for t in times_sorted:
            try:
                hour, minute = (int(p) for p in t.split(":"))
            except ValueError:
                continue
            candidate = start_local.replace(
                year=candidate_date.year, month=candidate_date.month, day=candidate_date.day,
                hour=hour, minute=minute, second=0, microsecond=0,
            )
            if start_local < candidate <= end_local:
                occurrences.append(candidate)
    return occurrences


@router.get("/upcoming", response_model=list[UpcomingJobRead])
def list_upcoming_jobs(
    hours: int = Query(default=24, ge=1, le=24 * 31),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[UpcomingJobRead]:
    """Alle faelligen geplanten Backup-Laeufe ueber alle Resource-Group-
    Policy-Verknuepfungen hinweg, chronologisch sortiert -- Grundlage fuer
    die Dashboard-Vorschau ('Jobs', siehe DashboardPage.tsx) UND die
    Backup-Kalenderansicht (Backup > Kalender, siehe CalendarTab.tsx).
    Der Zeitplan haengt an der Verknuepfung zwischen Resource Group und
    Policy, nicht an der Resource Group oder der Policy allein (siehe
    app.models.resource_group.ResourceGroupPolicyLink) -- dieselbe Resource
    Group kann so an mehrere Policies mit unterschiedlicher Kadenz haengen
    (z.B. ein CSV stuendlich UND woechentlich). Eine Verknuepfung mit z.B.
    einem HOURLY-Zeitplan erscheint mit jedem einzelnen Vorkommen im
    Fenster. Nutzt dieselbe _occurrences_within()-Logik wie
    run_scheduled_backups (app.core.scheduler), nur vorausschauend ueber ein
    ganzes Fenster statt rueckblickend seit dem letzten Check.

    Zwei Abfrage-Modi: `hours` (Default, ab jetzt vorausschauend -- fuer die
    Dashboard-Vorschau) ODER `start_date`+`end_date` (fester Kalendertag-
    Bereich, unabhaengig von "jetzt" -- fuer die Monatsansicht des Kalenders,
    die auch in die Vergangenheit oder mehrere Monate voraus blaettern
    koennen muss). Sind beide Datums-Parameter gesetzt, haben sie Vorrang
    vor `hours`."""
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.schedule_timezone)
    except Exception:
        tz = timezone.utc

    if start_date is not None and end_date is not None:
        # Startpunkt einen Tick vor Mitternacht des Start-Tages, da
        # _occurrences_within das Intervall exklusiv am Anfang behandelt
        # (start_local, end_local] -- sonst wuerde ein Vorkommen exakt um
        # 00:00 Uhr des ersten Tages verloren gehen.
        now_local = datetime.combine(start_date - timedelta(days=1), time.max, tzinfo=tz)
        end_local = datetime.combine(end_date, time.max, tzinfo=tz)
    else:
        now_local = datetime.now(tz)
        end_local = now_local + timedelta(hours=hours)

    links = db.query(ResourceGroupPolicyLink).filter(ResourceGroupPolicyLink.schedule_id.isnot(None)).all()
    upcoming: list[UpcomingJobRead] = []
    occurrences_by_schedule: dict[str, list[datetime]] = {}
    for link in links:
        if link.policy is None or not link.policy.enabled or link.resource_group is None:
            continue
        schedule = link.schedule
        if schedule is None or not schedule.times:
            continue
        if schedule.id not in occurrences_by_schedule:
            occurrences_by_schedule[schedule.id] = _occurrences_within(schedule, now_local, end_local)
        for occurrence in occurrences_by_schedule[schedule.id]:
            upcoming.append(
                UpcomingJobRead(
                    resource_group_id=link.resource_group_id,
                    resource_group_name=link.resource_group.name,
                    policy_id=link.policy_id,
                    policy_name=link.policy.name,
                    schedule_name=schedule.name,
                    consistency=link.policy.consistency,
                    next_run_at=occurrence.astimezone(timezone.utc),
                )
            )
    upcoming.sort(key=lambda u: u.next_run_at)
    return upcoming


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
        consistency=ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT,
        snapmirror_update=payload.snapmirror_update,
        snapmirror_label_id=payload.snapmirror_label_id,
        retention_type=payload.retention_type,
        retention_value=payload.retention_value,
        snapshot_locking_enabled=payload.snapshot_locking_enabled,
        snapshot_locking_days=payload.snapshot_locking_days,
        email_alert_on_failure=payload.email_alert_on_failure,
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
    policy.consistency = ConsistencyType.APPLICATION_CONSISTENT if payload.app_consistent else ConsistencyType.CRASH_CONSISTENT
    policy.snapmirror_update = payload.snapmirror_update
    policy.snapmirror_label_id = payload.snapmirror_label_id
    policy.retention_type = payload.retention_type
    policy.retention_value = payload.retention_value
    policy.snapshot_locking_enabled = payload.snapshot_locking_enabled
    policy.snapshot_locking_days = payload.snapshot_locking_days
    policy.email_alert_on_failure = payload.email_alert_on_failure
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


@router.get("/runs/{run_id}", response_model=BackupJobRun)
def get_job_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> BackupJobRun:
    """Einzelner Lauf inkl. Schritt-fuer-Schritt-Verlauf -- Grundlage fuer die
    Live-Fortschrittsanzeige waehrend ein Job noch laeuft (gepollt von
    RunningJobsIndicator.tsx, analog zu den Restore-/VM-Neuerstellungs-
    Wizards)."""
    run = db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return _to_run_read(run, include_steps=True)


@router.post("/runs/{run_id}/cancel", response_model=BackupJobRun)
def cancel_job_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_RUN)),
) -> BackupJobRun:
    """Fordert den Abbruch eines laufenden Backup-Laufs an -- setzt nur
    cancel_requested_at, der Status bleibt vorerst 'running' ('Abbruch
    angefordert' im Frontend). _execute_job_run prueft das Feld kooperativ
    zwischen den Schritten (siehe _cancel_requested) und stoppt VOR dem
    naechsten Schritt; ein bereits laufender einzelner WinRM-/NetApp-Aufruf
    kann nicht sofort unterbrochen werden (Python-Threads lassen sich nicht
    sicher von aussen abbrechen), laeuft aber wegen eigener Timeouts
    ohnehin in maximal ~10-50s aus. Bereits erstellte Hyper-V-Checkpoints
    werden in jedem Fall entfernt, unabhaengig vom Abbruch."""
    run = db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status != JobStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Lauf ist nicht aktiv (Status: {run.status.value})")
    if run.cancel_requested_at is None:
        run.cancel_requested_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return _to_run_read(run)


def _to_run_read(run: BackupRun, include_steps: bool = False) -> BackupJobRun:
    return BackupJobRun(
        id=run.id,
        job_id=run.policy_id,
        job_name=run.policy_name,
        resource_group_id=run.resource_group_id,
        resource_group_name=run.resource_group.name if run.resource_group else None,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        scope=run.scope,
        targets=run.targets or [],
        error_message=run.error_message,
        cancel_requested_at=run.cancel_requested_at,
        snapshots=list(run.snapshots),
        steps=[
            # s.status ist zur Laufzeit bereits ein reiner str (die Spalte ist
            # als String(20) angelegt, kein SQLAlchemy-Enum-Typ -- .value
            # existiert daher nur auf frisch zugewiesenen Enum-Werten VOR dem
            # naechsten DB-Read, nicht auf aus der DB geladenen Zeilen).
            BackupRunStepRead(step=s.step, label=s.label, status=str(s.status), message=s.message)
            for s in run.steps
        ]
        if include_steps
        else [],
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


def _csv_index(db: Session) -> tuple[dict[tuple[str, str], HyperVCsv], dict[str, NetAppLun], dict[str, str]]:
    """csv_by_key, luns_by_serial, cluster_names_by_id.

    csv_by_key ist ueber (HyperVCluster-ID, CSV-Name) statt nur den Namen
    indiziert -- zwei verschiedene Hyper-V-Cluster koennen (und tun das in
    der Praxis oft) ein CSV mit identischem Namen haben (z.B. beide
    "CSV01"), ein reiner Namens-Index wuerde dann eines der beiden CSVs
    stillschweigend verdecken (live beobachtet, siehe Backlog).

    luns_by_serial ist bewusst ueber die (stabile) Disk-Seriennummer indiziert
    und NICHT ueber die bei der Hyper-V-Discovery gespeicherte
    HyperVCsv.netapp_lun_id: NetAppLun.id ist eine bei JEDER NetApp-Discovery
    neu vergebene UUID (delete-then-reinsert), wird ein NetApp-Cluster also
    unabhaengig vom Hyper-V-Cluster neu discovert, waere die gespeicherte
    netapp_lun_id sofort veraltet und jede Backup-Ausfuehrung wuerde
    faelschlich "LUN nicht gefunden" melden. Die Seriennummer bleibt dagegen
    ueber Rediscoveries hinweg stabil (echte Hardware-Eigenschaft der LUN)."""
    csv_by_key = {(c.cluster_id, c.name): c for c in db.query(HyperVCsv).all()}
    luns_by_serial = {lun.serial_number: lun for lun in db.query(NetAppLun).all() if lun.serial_number}
    cluster_names_by_id = {c.id: c.ontap_cluster_name or c.name for c in db.query(NetAppCluster).all()}
    return csv_by_key, luns_by_serial, cluster_names_by_id


def _vhd_maps(db: Session) -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], set[str]]]:
    """(Cluster-ID, VM-Name) -> Menge der CSV-Namen, auf denen seine VHDs
    liegen, und umgekehrt (Cluster-ID, CSV-Name) -> Menge der VM-Namen
    darauf -- cluster-qualifiziert aus demselben Grund wie _csv_index."""
    vm_csvs: dict[tuple[str, str], set[str]] = defaultdict(set)
    csv_vms: dict[tuple[str, str], set[str]] = defaultdict(set)
    for vhd in db.query(HyperVVhd).all():
        if vhd.vm_name and vhd.csv_name:
            vm_csvs[(vhd.cluster_id, vhd.vm_name)].add(vhd.csv_name)
            csv_vms[(vhd.cluster_id, vhd.csv_name)].add(vhd.vm_name)
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


def _resolve_targets(
    db: Session, policy: BackupPolicy, resource_group_ids: set[str] | None = None
) -> tuple[list[_VolumeTarget], list[str]]:
    """Loest die Resource Groups einer Policy zu den betroffenen NetApp-Volumes
    auf. Mehrere VMs/CSVs auf demselben Volume werden zu einem gemeinsamen
    Ziel zusammengefasst (ein Snapshot deckt sie alle ab). Gibt zusaetzlich
    Warnungen fuer Mitglieder zurueck, die sich keinem NetApp-Volume zuordnen
    liessen (z.B. weil die Discovery noch nicht gelaufen ist).

    `resource_group_ids`, falls angegeben, beschraenkt die Aufloesung auf nur
    diese Resource Group(s) der Policy -- genutzt vom geplanten Lauf
    (run_scheduled_backups), der pro faelliger Resource-Group-Policy-
    Verknuepfung einzeln ausloest (siehe
    app.models.resource_group.ResourceGroupPolicyLink), statt wie beim
    manuellen "Jetzt ausfuehren" alle verknuepften Resource Groups auf
    einmal."""
    resource_groups = (
        [g for g in policy.resource_groups if g.id in resource_group_ids]
        if resource_group_ids is not None
        else policy.resource_groups
    )
    warnings: list[str] = []
    targets: dict[tuple[str, str, str], _VolumeTarget] = {}

    csv_by_key, luns_by_serial, cluster_names_by_id = _csv_index(db)
    vm_csvs, csv_vms = _vhd_maps(db)

    def _add(cluster_id: str, csv_name: str, vm_names: set[str]) -> None:
        csv = csv_by_key.get((cluster_id, csv_name))
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

    # Cluster-qualifiziert aufgeloest (siehe app.models.resource_group) --
    # ein noch nicht migrierter/mehrdeutiger Alt-Eintrag (Name ohne
    # Cluster-Zuordnung, z.B. weil er unter zwei Clustern gleich heisst)
    # wird bewusst als Warnung gemeldet statt geraten, um die urspruengliche
    # Namens-Kollision nicht im Fallback zu wiederholen.
    for group in resource_groups:
        if group.scope == BackupScope.VM:
            for member in group.members:
                resolved = resolve_member_key(member, set(vm_csvs.keys()))
                if resolved is None:
                    vm_name = parse_member_key(member)[1]
                    warnings.append(f"VM '{vm_name}': keine CSV/VHD-Zuordnung gefunden oder mehrdeutig (Hyper-V-Discovery pruefen)")
                    continue
                cluster_id, vm_name = resolved
                for csv_name in vm_csvs[resolved]:
                    _add(cluster_id, csv_name, {vm_name})
        elif group.scope == BackupScope.CSV:
            for member in group.members:
                resolved = resolve_member_key(member, set(csv_by_key.keys()))
                if resolved is None:
                    csv_name = parse_member_key(member)[1]
                    warnings.append(f"CSV '{csv_name}' nicht gefunden oder mehrdeutig (Hyper-V-Discovery pruefen)")
                    continue
                cluster_id, csv_name = resolved
                _add(cluster_id, csv_name, csv_vms.get(resolved, set()))
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


def _resolve_volume_keys_for_object(
    db: Session, scope: BackupScope, name: str, cluster_id: str | None = None
) -> list[tuple[str, str, str]]:
    """Loest eine einzelne VM oder ein einzelnes CSV (aktueller Discovery-
    Stand) zu den NetApp-Volumes auf, auf denen ihre Daten liegen -- fuer die
    'vorhandene Backups anzeigen'-Funktion im Inventory, unabhaengig von
    Resource Groups/Policies.

    `cluster_id` (der Hyper-V-Cluster der VM/des CSVs) macht die Aufloesung
    cluster-sicher, analog zu _resolve_targets -- OHNE das kann bei zwei
    Clustern mit gleichnamigem CSV (z.B. beide "CSV01", live beobachtet) das
    falsche CSV getroffen werden: eine reine Namens-Aufloesung liefert dann
    z.B. das Volume von Cluster B fuer eine VM, die tatsaechlich auf Cluster
    A liegt -- vorhandene, korrekt aufgezeichnete Snapshots dieser VM
    verschwinden dadurch aus der Liste (falsches Volume gefiltert), waehrend
    zufaellig noch passende, aber eigentlich fremde Snapshots auftauchen
    (live als echter Bug gefunden: 'RestoreTestVM_PG2' zeigte das Volume
    eines ANDEREN Clusters). Ohne cluster_id (Alt-Aufrufer) bleibt die
    bisherige mehrdeutige Bestfall-Aufloesung als Fallback bestehen."""
    csv_by_key, luns_by_serial, _ = _csv_index(db)

    keys: set[tuple[str, str, str]] = set()
    if cluster_id is not None:
        vm_csvs, _ = _vhd_maps(db)
        csv_names = {name} if scope == BackupScope.CSV else vm_csvs.get((cluster_id, name), set())
        for csv_name in csv_names:
            csv = csv_by_key.get((cluster_id, csv_name))
            if csv is None:
                continue
            key = _csv_volume_key(csv, luns_by_serial)
            if key is not None:
                keys.add(key)
        return list(keys)

    # Fallback ohne Cluster-Kontext (Alt-Aufrufer) -- mehrdeutig bei
    # gleichnamigen CSVs/VMs ueber mehrere Cluster hinweg, siehe Docstring.
    csv_by_any_name: dict[str, HyperVCsv] = {name_: csv for (_, name_), csv in csv_by_key.items()}
    csv_names = set()
    if scope == BackupScope.VM:
        vm_csvs, _ = _vhd_maps(db)
        for (_, vm_name), names in vm_csvs.items():
            if vm_name == name:
                csv_names |= names
    elif scope == BackupScope.CSV:
        csv_names = {name}
    for csv_name in csv_names:
        csv = csv_by_any_name.get(csv_name)
        if csv is None:
            continue
        key = _csv_volume_key(csv, luns_by_serial)
        if key is not None:
            keys.add(key)
    return list(keys)


@router.get("/backups", response_model=list[BackupSnapshotRead])
def list_backups_for_object(
    scope: BackupScope,
    name: str,
    cluster_id: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[BackupSnapshotRead]:
    """Vorhandene (erfolgreiche) Snapshots, die eine bestimmte VM oder ein
    bestimmtes CSV abdecken -- unabhaengig davon, ueber welche Policy sie
    entstanden sind. Wird vom Inventory (Rechtsklick -> Backups anzeigen)
    verwendet. `cluster_id` optional fuer Abwaertskompatibilitaet, sollte
    aber von jedem aktuellen Aufrufer mitgegeben werden (siehe
    _resolve_volume_keys_for_object)."""
    keys = set(_resolve_volume_keys_for_object(db, scope, name, cluster_id))
    if not keys:
        return []

    # RestoreInfraConfig-Schluessel einmal vorladen, um pro Ziel zu pruefen,
    # ob ein Restore davon ueberhaupt technisch moeglich ist (registrierter
    # Cluster reicht allein nicht, die Ziel-SVM braucht eine eigene
    # Restore-Infrastruktur, siehe Restore > Setup).
    infra_keys = {(c.netapp_cluster_id, c.svm_name) for c in db.query(RestoreInfraConfig).all()}

    def _restorable_destination(r: BackupRunSnapshot):
        return next(
            (d for d in r.destinations if d.present and d.destination_netapp_cluster_id and (d.destination_netapp_cluster_id, d.destination_svm_name) in infra_keys),
            None,
        )

    # Anders als frueher NICHT mehr auf success=True gefiltert: ein auf dem
    # Primaersystem geloeschter Snapshot (success=False, siehe
    # run_snapshot_reconciliation) bleibt sichtbar/waehlbar, solange er auf
    # einem restorebaren SnapMirror-Ziel noch vorhanden ist (Nutzer-Vorgabe:
    # Primaer vor Sekundaer, aber Sekundaer soll trotzdem nutzbar sein).
    rows = db.query(BackupRunSnapshot).all()
    matched = [
        r for r in rows
        if (r.netapp_cluster_id, r.svm_name or "", r.volume_name or "") in keys and (r.success or _restorable_destination(r) is not None)
    ]
    if scope == BackupScope.VM:
        # Ein Snapshot deckt ggf. mehrere VMs ab (gemeinsames CSV/Volume) --
        # per detach-vm kann eine VM manuell aus vm_names entfernt werden
        # (siehe unten), ohne den Snapshot selbst anzutasten. Ohne diesen
        # Filter wuerde sie ihn trotzdem weiterhin sehen, da oben nur ueber
        # den Volume-Key gematcht wird.
        matched = [r for r in matched if name in (r.vm_names or [])]
    matched.sort(key=lambda r: r.created_at, reverse=True)

    # Fuer VM-Scope zusaetzlich die zum Backup-Zeitpunkt gespeicherte VHD-
    # Liste dieser VM nachladen (BackupRunVmConfig, siehe trigger_job_run) --
    # der Restore-Wizard bietet darueber nur VHDs an, die in diesem
    # konkreten Snapshot tatsaechlich enthalten waren.
    vhds_by_run_id: dict[str, list[BackupSnapshotVhdRead]] = {}
    if scope == BackupScope.VM and matched:
        run_ids = {r.run_id for r in matched}
        configs = (
            db.query(BackupRunVmConfig)
            .filter(BackupRunVmConfig.run_id.in_(run_ids), BackupRunVmConfig.vm_name == name)
            .all()
        )
        for cfg in configs:
            vhds_by_run_id[cfg.run_id] = [
                BackupSnapshotVhdRead(
                    name=v.get("name", ""), path=v.get("path", ""), size_bytes=v.get("size_bytes"), used_bytes=v.get("used_bytes"),
                )
                for v in (cfg.vhds or [])
            ]

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
            vhds=vhds_by_run_id.get(r.run_id, []),
            restore_source="primary" if r.success else "secondary",
            destinations=[
                BackupSnapshotDestinationRead(
                    svm_name=d.destination_svm_name,
                    volume_name=d.destination_volume_name,
                    cluster_name=d.destination_netapp_cluster_name,
                    present=d.present,
                    restorable=bool(d.present and d.destination_netapp_cluster_id and (d.destination_netapp_cluster_id, d.destination_svm_name) in infra_keys),
                    last_checked_at=d.last_checked_at,
                )
                for d in r.destinations
            ],
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


@router.post("/backups/{snapshot_id}/detach-vm", status_code=status.HTTP_204_NO_CONTENT)
def detach_vm_from_backup_snapshot(
    snapshot_id: str, vm_name: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    """Entfernt nur die Zuordnung einer VM zu diesem Snapshot aus der DB --
    der Snapshot selbst bleibt auf der NetApp und fuer andere VMs/das CSV
    unveraendert bestehen. Fuer den Fall, dass ein Snapshot mehrere VMs
    abdeckt (gemeinsames CSV/Volume) und nur eine davon aus der Historie
    verschwinden soll. Kein response_model/Rueckgabewert -- das Frontend
    laedt nach Erfolg per invalidateQueries neu, statt die Antwort zu
    verwenden (BackupSnapshotRead erwartet Felder wie policy_name, die auf
    dem rohen BackupRunSnapshot-Objekt nicht direkt liegen, siehe
    list_backups_for_object oben, das ueber r.run.policy_name geht -- ein
    response_model hier waere bei der Serialisierung gescheitert)."""
    row = db.get(BackupRunSnapshot, snapshot_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot nicht gefunden")
    row.vm_names = [v for v in (row.vm_names or []) if v != vm_name]
    db.commit()


class _JobAlreadyRunningError(RuntimeError):
    pass


class _NoTargetsError(RuntimeError):
    pass


def _start_job_run(
    policy: BackupPolicy, db: Session, resource_group_ids: set[str] | None = None
) -> tuple[BackupRun, list[str]]:
    """Schneller, rein lokaler Teil eines Backup-Laufs: Ziele aufloesen, Lauf-
    Zeile + VM-Konfiguration anlegen. Bewusst OHNE WinRM-/NetApp-Aufrufe,
    damit dieser Teil synchron im Request bleiben kann -- die eigentliche,
    ggf. lange dauernde Ausfuehrung (Checkpoints/Snapshots/SnapMirror-Update)
    uebernimmt _execute_job_run als Hintergrund-Task (siehe trigger_job_run
    weiter unten), damit "Jetzt ausfuehren" sofort zurueckkehrt und der
    Fortschritt live gepollt werden kann (RunningJobsIndicator.tsx) statt den
    ganzen Request lang zu blockieren. Frueher lief das alles synchron in
    einem einzigen Request -- ein laufender Job war dadurch fuer die eigene
    Session unsichtbar, bis er komplett fertig war.

    `resource_group_ids`: siehe _resolve_targets -- wird vom geplanten Lauf
    (eine Resource Group, ihr eigener Zeitplan) gesetzt, bleibt bei einem
    manuellen "Jetzt ausfuehren" auf der ganzen Policy None. Der "laeuft
    bereits"-Schutz ist dann ebenfalls auf genau diese Resource Group
    beschraenkt (per BackupRun.resource_group_id), statt die ganze Policy zu
    blockieren -- zwei unterschiedlich geplante Resource Groups derselben
    Policy duerfen gleichzeitig laufen."""
    single_group_id = next(iter(resource_group_ids)) if resource_group_ids and len(resource_group_ids) == 1 else None
    already_running_query = db.query(BackupRun).filter(BackupRun.policy_id == policy.id, BackupRun.status == JobStatus.RUNNING)
    already_running_query = (
        already_running_query.filter(BackupRun.resource_group_id == single_group_id)
        if single_group_id is not None
        else already_running_query
    )
    if already_running_query.first() is not None:
        raise _JobAlreadyRunningError(f"Policy '{policy.name}' hat bereits einen laufenden Job.")

    targets, warnings = _resolve_targets(db, policy, resource_group_ids)
    if not targets:
        detail = "Keine gueltigen Backup-Ziele gefunden."
        if warnings:
            detail += " " + "; ".join(warnings)
        else:
            detail += " Der Policy ist keine Resource Group mit Zielen zugeordnet."
        raise _NoTargetsError(detail)

    resolved_groups = (
        [g for g in policy.resource_groups if g.id in resource_group_ids] if resource_group_ids is not None else policy.resource_groups
    )
    now = datetime.now(timezone.utc)
    all_targets = sorted({vm for t in targets for vm in t.vm_names} | {csv for t in targets for csv in t.csv_names})
    run = BackupRun(
        policy_id=policy.id,
        policy_name=policy.name,
        resource_group_id=single_group_id,
        status=JobStatus.RUNNING,
        consistency=policy.consistency.value,
        scope=resolved_groups[0].scope if resolved_groups else None,
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

    with _StepCtx(db, run.id, "targets", "Ziele aufgelöst", step_model=BackupRunStep) as ctx:
        ctx.row.message = ", ".join(all_targets) if all_targets else "(keine)"

    clusters_by_id = {c.id: c for c in db.query(NetAppCluster).all()}

    # VM-Konfiguration zum Backup-Zeitpunkt sichern (CPU/RAM/NICs/PCI/VHD-
    # Liste inkl. CSV/LUN-Zuordnung) -- kopiert aus der zuletzt discoverten
    # HyperVVm/HyperVVhd/HyperVCsv-DB, bewusst OHNE eigenen WinRM-Aufruf hier
    # (der Backup-Pfad soll die einfache, robuste storage-seitige
    # Snapshot-Erstellung bleiben). Grundlage fuer eine spaetere komplette
    # VM-Wiederherstellung sowie fuer die praezise VHD->LUN-Aufloesung beim
    # Restore (siehe BackupRunVmConfig, _execute_restore in
    # app.api.routes.restore) -- unabhaengig von einem zwischenzeitlichen
    # CSV/LUN-Umzug der VM. Eine fehlende/veraltete Discovery fuer eine VM
    # fuehrt nur zu einer unvollstaendigen Zeile, nicht zum Abbruch des Laufs.
    vm_names_in_run = sorted({vm for t in targets for vm in t.vm_names})
    if vm_names_in_run:
        cluster_ids_by_name: dict[str, str] = {}
        for cid, c in clusters_by_id.items():
            cluster_ids_by_name[c.name] = cid
            if c.ontap_cluster_name:
                cluster_ids_by_name[c.ontap_cluster_name] = cid

        hyperv_vms_by_name = {v.name: v for v in db.query(HyperVVm).filter(HyperVVm.name.in_(vm_names_in_run)).all()}
        hyperv_vhds_by_vm_uuid: dict[str, list[HyperVVhd]] = defaultdict(list)
        for vhd in db.query(HyperVVhd).all():
            if vhd.vm_uuid:
                hyperv_vhds_by_vm_uuid[vhd.vm_uuid].append(vhd)
        hyperv_csv_by_name = {c.name: c for c in db.query(HyperVCsv).all()}

        for vm_name in vm_names_in_run:
            hv_vm = hyperv_vms_by_name.get(vm_name)
            vhd_entries = []
            for vhd in (hyperv_vhds_by_vm_uuid.get(hv_vm.vm_uuid, []) if hv_vm and hv_vm.vm_uuid else []):
                csv = hyperv_csv_by_name.get(vhd.csv_name) if vhd.csv_name else None
                vhd_entries.append(
                    {
                        "name": win_basename(vhd.path),
                        "path": vhd.path,
                        "size_bytes": vhd.size_bytes,
                        "used_bytes": vhd.used_bytes,
                        "csv_name": vhd.csv_name,
                        "netapp_cluster_id": cluster_ids_by_name.get(csv.netapp_cluster_name) if csv and csv.netapp_cluster_name else None,
                        "netapp_cluster_name": csv.netapp_cluster_name if csv else None,
                        "svm_name": csv.netapp_svm_name if csv else None,
                        "volume_name": csv.netapp_volume_name if csv else None,
                        "lun_name": csv.netapp_lun_name if csv else None,
                    }
                )
            db.add(
                BackupRunVmConfig(
                    run_id=run.id, vm_name=vm_name, vm_uuid=hv_vm.vm_uuid if hv_vm else None,
                    hyperv_cluster_id=hv_vm.cluster_id if hv_vm else None,
                    cpu_count=hv_vm.cpu_count if hv_vm else None,
                    memory_startup_bytes=hv_vm.memory_startup_bytes if hv_vm else None,
                    memory_minimum_bytes=hv_vm.memory_minimum_bytes if hv_vm else None,
                    memory_maximum_bytes=hv_vm.memory_maximum_bytes if hv_vm else None,
                    dynamic_memory_enabled=hv_vm.dynamic_memory_enabled if hv_vm else None,
                    generation=hv_vm.generation if hv_vm else None,
                    host_name=hv_vm.host_name if hv_vm else None,
                    network_adapters=hv_vm.network_adapters if hv_vm else None,
                    pci_devices=hv_vm.pci_devices if hv_vm else None,
                    vhds=vhd_entries,
                )
            )
        db.commit()

    return run, warnings


def _cancel_requested(db: Session, run_id: str) -> bool:
    """Fragt frisch aus der DB ab (nicht das ggf. laenger im Speicher
    gehaltene BackupRun-ORM-Objekt, das Aenderungen aus einer ANDEREN
    Session/einem anderen Request sonst nicht sehen wuerde), ob fuer diesen
    Lauf ein Abbruch angefordert wurde (siehe POST /jobs/runs/{id}/cancel).
    Wird zwischen den einzelnen Schritten von _execute_job_run aufgerufen
    (je VM-Checkpoint, je Volume-Snapshot) -- ein bereits laufender
    einzelner WinRM-/NetApp-Aufruf kann dadurch NICHT unterbrochen werden
    (Python kann einen Thread nicht sicher von aussen abbrechen), der Lauf
    stoppt bestenfalls vor dem naechsten Schritt. Da jeder einzelne Aufruf
    ohnehin durch eigene Timeouts begrenzt ist (WinRM-Sessions ~10-50s,
    NetApp-SDK-HostConnection default 45s Read-Timeout), ist das der
    realistische Rahmen -- ein 'echter' harter Abbruch mitten in einem
    Aufruf ist mit dieser Architektur (synchrone Blocking-Calls in einem
    Thread) nicht sicher moeglich, ohne den Prozess zu riskieren."""
    return db.query(BackupRun.cancel_requested_at).filter(BackupRun.id == run_id).scalar() is not None


def _execute_job_run(run_id: str, initial_warnings: list[str]) -> None:
    """Fuehrt den eigentlichen, potenziell langwierigen Teil eines Backup-
    Laufs aus (Checkpoints, Snapshots, SnapMirror-Update, Checkpoint-
    Entfernung). Laeuft entweder als FastAPI-Hintergrund-Task (manuelles
    "Jetzt ausfuehren", siehe trigger_job_run) oder synchron direkt aus dem
    Scheduler heraus (run_scheduled_backups) -- oeffnet dafuer immer eine
    eigene DB-Session, analog zu _execute_restore/_execute_vm_recreate."""
    db = SessionLocal()
    policy = None  # fuer den aeussersten except-Block, falls die Zuweisung unten nie erreicht wird
    try:
        run = db.get(BackupRun, run_id)
        if run is None:
            return
        policy = db.get(BackupPolicy, run.policy_id)
        if policy is None:
            # Policy wurde geloescht -- kein Zugriff mehr auf
            # email_alert_on_failure moeglich, daher hier bewusst kein
            # E-Mail-Alert (seltener Randfall).
            run.status = JobStatus.FAILED
            run.error_message = "Policy wurde zwischenzeitlich geloescht"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        with _StepCtx(db, run.id, "run-started", "Backup gestartet", step_model=BackupRunStep) as ctx:
            ctx.row.message = f"Ziele: {', '.join(run.targets)}" if run.targets else "(keine Ziele)"

        # BUG (2026-09-02, live gemeldet und gefunden): frueher wurde hier IMMER
        # die ganze Policy aufgeloest, ohne die von _start_job_run bereits
        # ermittelte Resource-Group-Einschraenkung zu uebernehmen. Fuer eine
        # Policy, die an mehrere Resource Groups mit unterschiedlichem
        # Zeitplan haengt (siehe ResourceGroupPolicyLink -- genau der Zweck
        # der Zeitplan-pro-Verknuepfung-Funktion), sicherte dadurch JEDER
        # geplante Lauf IMMER ALLE verknuepften Resource Groups, unabhaengig
        # davon, welche Verknuepfung tatsaechlich faellig war. Live beobachtet:
        # Policy 'Silver_Hourly' haengt an 'Silver_CSV01' (Zeitplan xx:00) UND
        # 'Silver_CSV02' (Zeitplan xx:10) -- CSV01 wurde dadurch zusaetzlich
        # zu den eigenen xx:00-Laeufen auch bei jedem xx:10-Lauf von CSV02
        # unnoetig mitgesichert (und umgekehrt), sichtbar an ueberzaehligen
        # Snapshot-Zeitpunkten in "vorhandene Backups" fuer VMs auf CSV01.
        # Fix: dieselbe Einschraenkung wie bei _start_job_run anwenden --
        # run.resource_group_id ist bei einem geplanten Lauf (siehe
        # run_scheduled_backups: resource_group_ids={group.id}, stets genau
        # eine Gruppe) gesetzt, bei einem manuellen "Jetzt ausfuehren" auf der
        # ganzen Policy dagegen None -- reproduziert exakt dieselbe
        # Grundlage, mit der der Lauf urspruenglich gestartet wurde.
        resource_group_ids = {run.resource_group_id} if run.resource_group_id else None
        targets, _ = _resolve_targets(db, policy, resource_group_ids)
        clusters_by_id = {c.id: c for c in db.query(NetAppCluster).all()}
        volumes_by_key = {(v.cluster_id, v.svm_name, v.name): v for v in db.query(NetAppVolume).all()}
        snapshot_suffix = run.started_at.strftime("%Y%m%d%H%M%S")
        slug = _slugify(policy.name)
        label = policy.snapmirror_label.name if policy.snapmirror_label else None
        # Snapshot Locking: expiry_time an NetApp uebergeben, damit die Sperre
        # tatsaechlich wirkt (siehe create_snapshot in netapp_service.py) --
        # ab Erstellzeitpunkt des Laufs gerechnet, nicht ab Snapshot-Erstellung
        # je Ziel, damit alle Snapshots eines Laufs zum selben Zeitpunkt
        # ablaufen, unabhaengig von kleinen Zeitversaetzen zwischen Zielen.
        snapshot_expiry = (
            run.started_at + timedelta(days=policy.snapshot_locking_days)
            if policy.snapshot_locking_enabled and policy.snapshot_locking_days
            else None
        )
        vm_names_in_run = sorted({vm for t in targets for vm in t.vm_names})
        hyperv_vms_by_name = {v.name: v for v in db.query(HyperVVm).filter(HyperVVm.name.in_(vm_names_in_run)).all()}

        errors: list[str] = list(initial_warnings)
        was_cancelled = False

        # Applikationskonsistenz: pro betroffener VM VORHER einen Hyper-V-
        # Production-Checkpoint erzeugen (VSS-Quiesce) -- die dabei eingefrorene
        # Basis-VHDX enthaelt danach exakt den konsistenten Stand, den der
        # gleich folgende Storage-Snapshot festhaelt. Alle Checkpoints werden
        # erst NACH allen Snapshots (unten) wieder entfernt, damit eine VM mit
        # Disks auf mehreren Volumes fuer alle ihre Snapshots denselben
        # eingefrorenen Stand zeigt. Scheitert der Checkpoint fuer eine VM, wird
        # das vermerkt, die VM wird aber trotzdem (crash-konsistent) gesichert
        # statt den ganzen Lauf abzubrechen.
        checkpoint_name = f"hvnb_{slug}_{snapshot_suffix}"
        active_checkpoints: list[tuple[HyperVService, object, str]] = []
        if policy.consistency == ConsistencyType.APPLICATION_CONSISTENT and vm_names_in_run:
            settings = get_settings()
            hyperv_clusters_by_id = {c.id: c for c in db.query(HyperVCluster).all()}
            for vm_name in vm_names_in_run:
                if _cancel_requested(db, run.id):
                    was_cancelled = True
                    break
                hv_vm = hyperv_vms_by_name.get(vm_name)
                hv_cluster = hyperv_clusters_by_id.get(hv_vm.cluster_id) if hv_vm else None
                if hv_vm is None or hv_cluster is None:
                    msg = "Hyper-V-Cluster nicht gefunden, Checkpoint uebersprungen (Backup laeuft crash-konsistent weiter)"
                    errors.append(f"VM '{vm_name}': {msg}")
                    with _StepCtx(db, run.id, f"checkpoint-create-{vm_name}", f"Checkpoint erstellen: {vm_name}", step_model=BackupRunStep) as ctx:
                        ctx.row.status = RestoreStepStatus.SKIPPED
                        ctx.row.message = msg
                    continue
                try:
                    with _StepCtx(db, run.id, f"checkpoint-create-{vm_name}", f"Checkpoint erstellen: {vm_name}", step_model=BackupRunStep) as ctx:
                        hv_service = HyperVService(settings, hv_cluster.management_address, use_https=hv_cluster.use_https)
                        hv_password = decrypt_secret(hv_cluster.encrypted_password)
                        cno_session = hv_service.connect(hv_cluster.username, hv_password, read_timeout_sec=15, operation_timeout_sec=10)
                        owner_node = hv_service.get_vm_owner_node(cno_session, vm_name) or hv_vm.host_name
                        node_address = hv_service.resolve_node_address(cno_session, owner_node)
                        node_service = HyperVService(settings, node_address, use_https=hv_cluster.use_https)
                        node_session = node_service.connect(hv_cluster.username, hv_password)
                        node_service.create_checkpoint(node_session, vm_name, checkpoint_name, policy.consistency)
                        active_checkpoints.append((node_service, node_session, vm_name))
                        ctx.row.message = f"Checkpoint '{checkpoint_name}' auf Knoten '{node_address}' erstellt"
                except Exception as exc:
                    errors.append(f"VM '{vm_name}': Checkpoint konnte nicht erstellt werden ({exc}) -- Backup laeuft crash-konsistent weiter")

        for target in targets:
            if was_cancelled:
                break
            if _cancel_requested(db, run.id):
                was_cancelled = True
                break
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

            target_label = target.volume_name or ", ".join(sorted(target.csv_names)) or "?"

            if cluster is None or not target.svm_name or not target.volume_name:
                row.success = False
                row.error_message = "NetApp-Cluster oder -Volume nicht auflösbar"
                errors.append(f"{target.volume_name or '?'}: {row.error_message}")
                db.add(row)
                with _StepCtx(db, run.id, f"snapshot-{target_label}", f"Snapshot erstellen: {target_label}", step_model=BackupRunStep) as ctx:
                    ctx.row.status = RestoreStepStatus.SKIPPED
                    ctx.row.message = row.error_message
                continue

            snapshot_name = f"hvnb_{slug}_{snapshot_suffix}"
            try:
                with _StepCtx(db, run.id, f"snapshot-{target_label}", f"Snapshot erstellen: {target_label}", step_model=BackupRunStep) as ctx:
                    service = _netapp_service_for(cluster)
                    snap = service.create_snapshot(
                        target.volume_name, target.svm_name, snapshot_name,
                        snapmirror_label=label, expiry_time=snapshot_expiry,
                    )
                    row.snapshot_name = snap.name
                    row.snapshot_uuid = snap.uuid
                    row.success = True
                    lock_note = f", gesperrt bis {snapshot_expiry.strftime('%Y-%m-%d %H:%M UTC')}" if snapshot_expiry else ""
                    ctx.row.message = f"Snapshot '{snap.name}' auf Volume '{target.volume_name}' @ {target.svm_name} erstellt{lock_note}"
            except Exception as exc:
                row.success = False
                row.error_message = str(exc)
                errors.append(f"{target.volume_name}: {exc}")
            db.add(row)

            # SnapMirror-Update anstossen, falls die Policy das vorsieht --
            # eigener try/except (nicht Teil des Snapshot-try/except oben), damit
            # ein Fehler hier nicht faelschlich den erfolgreich erstellten
            # Snapshot als fehlgeschlagen markiert. Nutzt die per Discovery
            # bereits bekannte Beziehung (kein zusaetzlicher Live-Aufruf zum
            # Aufloesen noetig, siehe auch POST /api/resource-groups/
            # check-snapmirror, das dieselbe Tabelle fuer die Praesenzpruefung
            # im Policy-/Protection-Group-Formular nutzt). Fehlt die Beziehung
            # oder schlaegt der Trigger fehl, wird das wie ein Checkpoint-Fehler
            # oben als Warnung vermerkt (Lauf insgesamt FAILED, der Snapshot
            # selbst bleibt aber gueltig und restorebar) -- der Nutzer soll das
            # sehen und ueber den Check-Panel-Hinweis die Beziehung anlegen.
            if row.success and policy.snapmirror_update:
                try:
                    with _StepCtx(
                        db, run.id, f"snapmirror-{target_label}", f"SnapMirror-Update: {target_label}", step_model=BackupRunStep,
                    ) as ctx:
                        rel = (
                            db.query(NetAppSnapMirrorRelationship)
                            .filter(NetAppSnapMirrorRelationship.source_path == f"{target.svm_name}:{target.volume_name}")
                            .first()
                        )
                        if rel is None or not rel.uuid:
                            msg = "Kein SnapMirror-Update ausgeloest (keine Beziehung konfiguriert)"
                            errors.append(f"{target.volume_name}: {msg}")
                            ctx.row.status = RestoreStepStatus.SKIPPED
                            ctx.row.message = msg
                        else:
                            sm_result = service.trigger_snapmirror_update(rel.uuid)
                            if not sm_result.success:
                                raise RuntimeError(sm_result.message)
                            ctx.row.message = f"Update fuer Beziehung {rel.source_path} -> {rel.destination_path} ausgeloest"
                except Exception as exc:
                    errors.append(f"{target.volume_name}: SnapMirror-Update fehlgeschlagen ({exc})")

        for node_service, node_session, vm_name in active_checkpoints:
            try:
                with _StepCtx(db, run.id, f"checkpoint-remove-{vm_name}", f"Checkpoint entfernen: {vm_name}", step_model=BackupRunStep) as ctx:
                    result = node_service.remove_checkpoint(node_session, vm_name, checkpoint_name)
                    if not result.success:
                        raise RuntimeError(result.error)
                    ctx.row.message = f"Checkpoint '{checkpoint_name}' entfernt"
            except Exception as exc:
                errors.append(f"VM '{vm_name}': Checkpoint-Entfernung fehlgeschlagen: {exc}")

        run.finished_at = datetime.now(timezone.utc)
        if was_cancelled:
            run.status = JobStatus.CANCELLED
            run.error_message = "; ".join(["Manuell abgebrochen", *errors]) if errors else "Manuell abgebrochen"
        else:
            run.status = JobStatus.FAILED if errors else JobStatus.SUCCEEDED
            run.error_message = "; ".join(errors) if errors else None
        db.commit()
        # Direkt konstruiert statt ueber _StepCtx -- dessen __exit__ fuellt
        # eine leere Nachricht sonst automatisch mit 'OK', was hier bei
        # Erfolg ('Backup erfolgreich beendet -- OK') unnoetig doppelt
        # waere. label traegt den Status, message (nur bei Fehlern/Abbruch)
        # die Ursache -- logs.py haengt sie dann als 'label: message' an.
        if run.status == JobStatus.CANCELLED:
            final_label, final_message, final_step_status = "Backup abgebrochen", run.error_message, RestoreStepStatus.SKIPPED
        elif run.status == JobStatus.FAILED:
            final_label, final_message, final_step_status = "Backup mit Fehlern beendet", run.error_message, RestoreStepStatus.ERROR
        else:
            final_label, final_message, final_step_status = "Backup erfolgreich beendet", None, RestoreStepStatus.SUCCESS
        db.add(BackupRunStep(run_id=run.id, step="run-finished", label=final_label, message=final_message, status=final_step_status))
        db.commit()
        # CANCELLED ist bewusst kein Fehler-Alarm wert (der Nutzer hat den
        # Lauf ja absichtlich gestoppt) -- nur ein echtes FAILED benachrichtigt.
        if run.status == JobStatus.FAILED:
            notify_backup_failure(db, run.policy_name, run.id, run.error_message, run.targets, policy.email_alert_on_failure)
    except Exception as exc:
        run = db.get(BackupRun, run_id)
        if run is not None:
            run.status = JobStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.add(
                BackupRunStep(
                    run_id=run.id, step="run-finished", label="Backup mit Fehlern beendet",
                    message=run.error_message, status=RestoreStepStatus.ERROR,
                )
            )
            db.commit()
            notify_backup_failure(db, run.policy_name, run.id, run.error_message, run.targets, bool(policy and policy.email_alert_on_failure))
    finally:
        db.close()


@router.post("/{job_id}/run", response_model=list[BackupJobRun], status_code=status.HTTP_202_ACCEPTED)
def trigger_job_run(
    job_id: str, background_tasks: BackgroundTasks,
    resource_group_id: list[str] | None = Query(default=None),
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_RUN)),
) -> list[BackupJobRun]:
    """resource_group_id (optional, wiederholbar): beschraenkt den Lauf auf
    genau diese Resource Group(s) statt (Default, Parameter weggelassen)
    alle mit der Policy verknuepften auf einmal auszuloesen. Drei Faelle:
    - weggelassen: klassischer Ganze-Policy-Lauf, EIN Lauf mit
      resource_group_id=NULL (unveraendertes Verhalten).
    - genau eine ID: 'Jetzt nachholen'-Button beim backup_missed-Alarm
      (siehe app.core.scheduler.run_alert_check) sowie der Normalfall im
      Auswahldialog (Policy mit nur einer verknuepften Gruppe) -- EIN Lauf,
      przise dieser Gruppe zugeordnet.
    - mehrere IDs: Auswahldialog bei 'Jetzt ausfuehren' auf einer Policy
      mit mehreren verknuepften Protection Groups (siehe
      ResourceGroupPickerModal im Frontend). Nutzer-Vorgabe (2026-09-03):
      JE ausgewaehlter Gruppe ein EIGENER Lauf (wie ein geplanter Lauf pro
      faelliger Verknuepfung, siehe run_scheduled_backups), statt eines
      gemeinsamen Laufs mit resource_group_id=NULL -- damit Job-Verlauf
      und Alarme praezise pro Gruppe nachvollziehbar bleiben, statt
      'Alle Gruppen' anzuzeigen, obwohl nur eine Auswahl gemeint war. Ist
      fuer eine der Gruppen bereits ein Lauf dieser Policy aktiv, wird nur
      diese eine uebersprungen (Fehler geloggt), die anderen laufen
      trotzdem an -- ein einzelner blockierter Lauf soll nicht die ganze
      Auswahl verhindern."""
    policy = db.get(BackupPolicy, job_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy nicht gefunden")

    # Reihenfolge erhalten, Duplikate entfernen (falls das Frontend aus
    # Versehen dieselbe ID zweimal mitschickt).
    group_ids = list(dict.fromkeys(resource_group_id)) if resource_group_id else None

    created_runs: list[BackupRun] = []
    errors: list[str] = []

    if group_ids and len(group_ids) > 1:
        for group_id in group_ids:
            try:
                run, warnings = _start_job_run(policy, db, resource_group_ids={group_id})
            except (_JobAlreadyRunningError, _NoTargetsError) as exc:
                errors.append(str(exc))
                continue
            created_runs.append(run)
            background_tasks.add_task(_execute_job_run, run.id, warnings)
    else:
        single_group_ids = {group_ids[0]} if group_ids else None
        try:
            run, warnings = _start_job_run(policy, db, resource_group_ids=single_group_ids)
        except _JobAlreadyRunningError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except _NoTargetsError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        created_runs.append(run)
        background_tasks.add_task(_execute_job_run, run.id, warnings)

    if not created_runs:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="; ".join(errors) or "Keine Läufe gestartet")

    return [_to_run_read(r) for r in created_runs]
