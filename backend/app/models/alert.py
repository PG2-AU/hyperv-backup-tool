"""Generisches Warnungs-Modell fuer alles, was im Dashboard unter
'Warnungen' gezaehlt wird und in der neuen Alarme-Seite als aktuelle/
historische Liste erscheint (siehe app.core.scheduler.run_alert_check):
Kapazitaets-Schwellwerte (Volume/LUN), ungesunde Hyper-V-/NetApp-Cluster,
ungesunde SnapMirror-Beziehungen.

Fehlgeschlagene Backup-Laeufe werden bewusst NICHT hier persistiert (siehe
list_alerts in app.api.routes.alerts) -- BackupRun ist bereits die
vollstaendige historische Quelle dafuer (Job-Verlauf), eine zweite Kopie
wuerde nur auseinanderlaufen koennen. Sie werden dort stattdessen live aus
BackupRun abgeleitet und in dieselbe Antwortliste eingemischt."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class AlertType(str, enum.Enum):
    CAPACITY_VOLUME = "capacity_volume"
    CAPACITY_LUN = "capacity_lun"
    HYPERV_CLUSTER_UNHEALTHY = "hyperv_cluster_unhealthy"
    NETAPP_CLUSTER_UNHEALTHY = "netapp_cluster_unhealthy"
    SNAPMIRROR_UNHEALTHY = "snapmirror_unhealthy"
    SNAPMIRROR_LAG_EXCEEDED = "snapmirror_lag_exceeded"
    HYPERV_NODE_UNREACHABLE = "hyperv_node_unreachable"
    BACKUP_MISSED = "backup_missed"
    SCHEDULE_COLLISION = "schedule_collision"
    HYPERV_ORPHAN_CHECKPOINT = "hyperv_orphan_checkpoint"
    HYPERV_VM_MULTI_CSV = "hyperv_vm_multi_csv"


class AlertScope(str, enum.Enum):
    ALL = "all"
    # Nur Volumes/LUNs/SnapMirror-Beziehungen, die ueber eine discoverte
    # HyperVCsv tatsaechlich als Hyper-V-Storage genutzt werden -- alle
    # anderen, im NetApp-Cluster ebenfalls vorhandenen Objekte (andere
    # Workloads auf demselben Cluster) werden dann nicht mitgezaehlt.
    HYPERV_REFERENCED = "hyperv_referenced"


class AlertStatus(str, enum.Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class Alert(Base):
    """object_key identifiziert das betroffene Objekt eindeutig UND stabil
    (Volume-/LUN-/Cluster-/Beziehungs-UUID bzw. -ID) -- verhindert doppelte
    aktive Alarme fuer dasselbe Objekt bei wiederholten Checks."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    alert_type: Mapped[AlertType] = mapped_column(Enum(AlertType))
    object_key: Mapped[str] = mapped_column(String(255))
    object_name: Mapped[str] = mapped_column(String(255))
    netapp_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    netapp_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hyperv_cluster_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(String(500))
    threshold_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triggered_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.ACTIVE)
    triggered_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nur bei backup_missed gesetzt -- Grundlage fuer den "Jetzt nachholen"-
    # Button (siehe app.api.routes.alerts), der genau diese Resource
    # Group + Policy erneut auslöst, ohne dass der Nutzer beides erst
    # wieder manuell zusammensuchen muss.
    resource_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # vm_name: bei hyperv_orphan_checkpoint UND hyperv_vm_multi_csv gesetzt.
    # Bei hyperv_orphan_checkpoint zusaetzlich Grundlage fuer den
    # "Checkpoint löschen"-Button (siehe app.api.routes.alerts), der denselben
    # Endpunkt wie die Inventory-Aktion aufruft (POST /api/vms/{cluster}/
    # {vm}/checkpoints/{checkpoint}/delete, siehe app.api.routes.vms).
    vm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AlertConfig(Base):
    """Singleton-Konfiguration fuer die Alarme-Seite (Settings > Alarms):
    Schwellwerte pro Kategorie sowie der Sichtbarkeits-Scope (alle
    Storage-Objekte vs. nur die tatsaechlich vom Hyper-V-Cluster genutzten).
    Cluster-Gesundheit hat bewusst keinen eigenen Schwellwert -- sie folgt
    direkt dem bereits discoverten 'health'-Zustand."""

    __tablename__ = "alert_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    volume_threshold_percent: Mapped[int] = mapped_column(Integer, default=90)
    lun_threshold_percent: Mapped[int] = mapped_column(Integer, default=90)
    snapmirror_lag_threshold_hours: Mapped[int] = mapped_column(Integer, default=4)
    # Wie lange nach dem faelligen Zeitpunkt eines geplanten Laufs gewartet
    # wird, bevor er als verpasst gilt (siehe run_alert_check) -- muss
    # groesser als die normale Verzoegerung durch sequenzielle Abarbeitung/
    # das Nachhol-Fenster in run_scheduled_backups sein (max. 15min dort),
    # sonst wuerden voellig normale, nur leicht verspaetete Laeufe
    # faelschlich als verpasst gemeldet.
    backup_missed_grace_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # Zwei Zeitplan-Vorkommen, die an einem gemeinsam moeglichen Kalendertag
    # innerhalb dieser Spanne liegen, gelten als 'Kollision' (siehe
    # app.core.scheduler._find_schedule_collisions).
    schedule_collision_window_minutes: Mapped[int] = mapped_column(Integer, default=15)
    # Ein Checkpoint gilt erst als verwaist, wenn er laenger als diese Spanne
    # besteht -- ein normaler applikationskonsistenter Backup-Checkpoint
    # existiert nur Sekunden bis wenige Minuten (siehe _execute_job_run),
    # alles darueber hinaus ist praktisch sicher ein Ueberbleibsel.
    orphan_checkpoint_grace_minutes: Mapped[int] = mapped_column(Integer, default=60)
    # Wie oft der periodische Warnungs-Check (run_alert_check) automatisch
    # laeuft -- Aenderung hier wird sofort per scheduler.reschedule_job() auf
    # die laufende APScheduler-Job-ID "alert-check" angewendet, kein
    # Container-Neustart noetig (siehe update_alert_config in
    # app.api.routes.alerts, analog zu SchedulerConfig).
    alert_check_interval_minutes: Mapped[int] = mapped_column(Integer, default=5)
    scope: Mapped[AlertScope] = mapped_column(Enum(AlertScope), default=AlertScope.ALL)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
