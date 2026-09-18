import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime


class BackupScope(str, enum.Enum):
    VM = "vm"
    CSV = "csv"
    LUN = "lun"
    # SMB3/CIFS-gehostete VMs (Backlog #22) -- gebuendelt nach NetApp-CIFS-
    # Freigabe, gleiches Batching-Prinzip wie CSV.
    SMB_SHARE = "smb_share"


class ConsistencyType(str, enum.Enum):
    APPLICATION_CONSISTENT = "ApplicationConsistent"
    CRASH_CONSISTENT = "CrashConsistent"


class RetentionType(str, enum.Enum):
    DAYS = "days"
    COUNT = "count"


class BackupPolicy(Base):
    """Backup-Policy (frueher 'Job-Definition'): wiederverwendbare Regel aus
    Konsistenz-Modus, SnapMirror-Verhalten, Retention und optionalem Snapshot
    Locking. Die VM/CSV-Zuordnung erfolgt ueber ResourceGroups, die mit
    dieser Policy verknuepft werden (siehe app.models.resource_group) --
    Resource Group + Policy ergeben zusammen die Backup-Definition.

    Der Zeitplan haengt NICHT hier, sondern an der ResourceGroup (siehe dort)
    -- eine gealterte 'schedule_id'-Spalte kann in der DB-Tabelle noch aus
    frueheren Versionen vorhanden sein, wird von diesem Modell aber bewusst
    nicht mehr gemappt (siehe Migration in init_db.py, die bestehende Werte
    beim Umstieg auf die verknuepften ResourceGroups uebertraegt)."""

    __tablename__ = "backup_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    consistency: Mapped[ConsistencyType] = mapped_column(Enum(ConsistencyType), default=ConsistencyType.CRASH_CONSISTENT)
    snapmirror_update: Mapped[bool] = mapped_column(Boolean, default=False)
    snapmirror_label_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("snapmirror_labels.id"), nullable=True)
    retention_type: Mapped[RetentionType] = mapped_column(Enum(RetentionType), default=RetentionType.COUNT)
    retention_value: Mapped[int] = mapped_column(Integer, default=7)
    snapshot_locking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot_locking_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrocluster_aware: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ob ein fehlgeschlagener Lauf dieser Policy per E-Mail gemeldet wird
    # (siehe app.services.email_service.notify_backup_failure) -- pro Policy
    # statt global schaltbar, da nicht jede Policy gleich kritisch ist.
    # Setzt zusaetzlich voraus, dass SMTP unter Settings > E-Mail konfiguriert
    # und aktiviert ist.
    email_alert_on_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    # `enabled=False` dient seit 2026-09-14 (Backlog-Punkt 52) auch als die
    # "Pausieren"-Funktion fuer Policies (GUI-Status zeigte "pausiert" bei
    # enabled=False schon vorher an -- bewusst dasselbe Feld wiederverwendet
    # statt ein zweites, verwirrendes Pausier-Feld einzufuehren). paused_
    # since/paused_until tragen dafuer dasselbe Zeitfenster-Muster wie
    # ResourceGroup.paused_since/-until nach: verhindert, dass ein
    # Wiedereinschalten nach laengerer Pause die backup_missed-Erkennung
    # (scheduler.py) mit einer Flut falscher 'verpasst'-Alarme fuer jedes
    # waehrend der Pause ausgelassene Vorkommen ueberschwemmt.
    # paused_until bleibt waehrend einer laufenden Pause leer und wird erst
    # beim Wiedereinschalten auf den aktuellen Zeitpunkt gesetzt.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    snapmirror_label = relationship("SnapMirrorLabel")
    # viewonly: das Schreiben der Verknuepfung (inkl. ihres Zeitplans) laeuft
    # ueber ResourceGroup.policy_links (siehe app.models.resource_group).
    resource_groups = relationship("ResourceGroup", secondary="resource_group_policies", viewonly=True)
