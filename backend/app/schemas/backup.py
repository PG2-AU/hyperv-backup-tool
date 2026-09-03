from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.backup_policy import BackupScope, ConsistencyType, RetentionType
from app.models.backup_run import JobStatus
from app.schemas.snapmirror_label import SnapMirrorLabelRead


class BackupPolicyWrite(BaseModel):
    """Gemeinsames Payload-Schema fuer Anlegen und Bearbeiten einer Backup-Policy."""

    name: str
    app_consistent: bool = False
    snapmirror_update: bool = False
    snapmirror_label_id: str | None = None
    retention_type: RetentionType
    retention_value: int
    snapshot_locking_enabled: bool = False
    snapshot_locking_days: int | None = None
    email_alert_on_failure: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "BackupPolicyWrite":
        if self.retention_value <= 0:
            raise ValueError("Retention-Anzahl muss groesser als 0 sein")

        if self.snapshot_locking_enabled:
            if not self.snapshot_locking_days or self.snapshot_locking_days <= 0:
                raise ValueError("Anzahl Tage fuer Snapshot Locking erforderlich, wenn aktiviert")
        elif self.snapshot_locking_days is not None:
            raise ValueError("Anzahl Tage fuer Snapshot Locking ist nur zulaessig, wenn Snapshot Locking aktiviert ist")

        return self


class BackupPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    consistency: ConsistencyType
    snapmirror_update: bool
    snapmirror_label_id: str | None = None
    snapmirror_label: SnapMirrorLabelRead | None = None
    retention_type: RetentionType
    retention_value: int
    snapshot_locking_enabled: bool
    snapshot_locking_days: int | None = None
    metrocluster_aware: bool
    email_alert_on_failure: bool
    enabled: bool
    created_at: datetime


class UpcomingJobRead(BaseModel):
    """Naechster faelliger Lauf einer geplanten Resource Group (der Zeitplan
    haengt an der ResourceGroup, nicht mehr an der Policy, siehe
    app.models.resource_group) fuer eine ihrer verknuepften Policies -- fuer
    die Dashboard-Vorschau ('Jobs'), siehe list_upcoming_jobs in jobs.py."""

    resource_group_id: str
    resource_group_name: str
    policy_id: str
    policy_name: str
    schedule_name: str
    consistency: ConsistencyType
    next_run_at: datetime


class BackupRunSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    netapp_cluster_name: str | None = None
    svm_name: str | None = None
    volume_name: str | None = None
    csv_names: list[str] = []
    lun_names: list[str] = []
    vm_names: list[str] = []
    snapshot_name: str | None = None
    snapshot_uuid: str | None = None
    success: bool
    error_message: str | None = None


class BackupSnapshotVhdRead(BaseModel):
    """Eine VHDX dieser VM, wie sie zum Backup-Zeitpunkt vorlag (aus
    BackupRunVmConfig) -- damit der Restore-Wizard nur VHDs zur Auswahl
    anbietet, die in diesem konkreten Snapshot tatsaechlich enthalten sind,
    statt aus dem aktuell-live VM-Zustand (der sich seitdem geaendert haben
    kann, z.B. neue Disks oder ein CSV/LUN-Umzug)."""

    name: str
    path: str
    size_bytes: int | None = None
    # Belegter Platz zum Backup-Zeitpunkt (Get-VHD -> FileSize) -- Basis fuer
    # die CSV-Kapazitaetsabschaetzung beim Restore, siehe VhdInfo.used_bytes
    # in schemas/vm.py. Bei vor dieser Ergaenzung erstellten Laeufen fehlt
    # der Schluessel im gespeicherten JSON -> None (Frontend faellt dann auf
    # size_bytes zurueck).
    used_bytes: int | None = None


class BackupSnapshotDestinationRead(BaseModel):
    """Eine per SnapMirror discoverte Ziel-Beziehung fuer diesen Snapshot
    (siehe BackupRunSnapshotDestination) -- 'present' und 'restorable'
    unterscheiden sich, weil Praesenz auch fuer nicht in dieser App
    registrierte Ziel-Cluster getrackt werden kann, ein Restore davon aber
    eine registrierte RestoreInfraConfig fuer die Ziel-SVM braucht."""

    svm_name: str
    volume_name: str
    cluster_name: str | None = None
    present: bool
    restorable: bool
    last_checked_at: datetime


class BackupSnapshotRead(BaseModel):
    """Ein vorhandener Snapshot, der eine bestimmte VM oder ein bestimmtes CSV
    abdeckt (fuer die 'Backups anzeigen'-Funktion im Inventory) -- unabhaengig
    davon, ueber welche Policy/Resource-Group er entstanden ist."""

    id: str
    run_id: str
    policy_name: str
    consistency: ConsistencyType
    created_at: datetime
    netapp_cluster_name: str | None = None
    svm_name: str | None = None
    volume_name: str | None = None
    csv_names: list[str] = []
    vm_names: list[str] = []
    snapshot_name: str | None = None
    snapshot_uuid: str | None = None
    vhds: list[BackupSnapshotVhdRead] = []
    destinations: list[BackupSnapshotDestinationRead] = []
    # "primary": Snapshot ist noch auf dem urspruenglichen (Quell-)Volume
    # vorhanden, ein Restore verwendet IMMER dieses (Nutzer-Vorgabe: Primaer
    # vor Sekundaer). "secondary": auf der Quelle nicht mehr vorhanden
    # (per Abgleich erkannt geloescht), Restore weicht automatisch auf ein
    # noch vorhandenes SnapMirror-Ziel aus. "unavailable": weder noch --
    # dieser Snapshot ist ueberhaupt nicht mehr restorebar.
    restore_source: str = "primary"


class BackupRunStepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None


class BackupJobRun(BaseModel):
    id: str
    job_id: str | None = None
    job_name: str
    # Nur bei einem geplanten Lauf gesetzt (siehe BackupRun.resource_group_id)
    # -- ein manuelles "Jetzt ausfuehren" auf der ganzen Policy (potenziell
    # mehrere Resource Groups zusammen) laesst das leer. Fuer die Anzeige in
    # den Zeitstrahl-Ansichten (Dashboard/Backup > Kalender): dort faellt
    # die Beschriftung in diesem Fall auf job_name (Policy-Name) zurueck.
    resource_group_id: str | None = None
    resource_group_name: str | None = None
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    scope: BackupScope | None = None
    targets: list[str]
    error_message: str | None = None
    # Gesetzt sobald POST /jobs/runs/{id}/cancel aufgerufen wurde, auch
    # WAEHREND der Lauf noch status=running zeigt -- Frontend kann so
    # "Abbruch angefordert..." anzeigen, bevor der Lauf tatsaechlich stoppt
    # (kooperativ zwischen den Schritten, siehe _execute_job_run).
    cancel_requested_at: datetime | None = None
    snapshots: list[BackupRunSnapshotRead] = []
    # Nur befuellt vom Einzel-Lauf-Endpunkt (GET /jobs/runs/{id}), NICHT von
    # der Liste (GET /jobs/runs) -- sonst waechst deren Antwort mit jedem
    # historischen Lauf unnoetig. Grundlage fuer die Live-Fortschrittsanzeige
    # waehrend ein Job noch laeuft (siehe RunningJobsIndicator.tsx).
    steps: list[BackupRunStepRead] = []
