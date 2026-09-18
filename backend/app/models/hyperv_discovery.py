"""Persistierte Ergebnisse der Hyper-V-Cluster-Discovery (VMs inkl. ihrer
VHDs). Pro Cluster wird bei jedem Discovery-Lauf, der mindestens einen
erreichbaren Knoten hatte, die komplette VM-/VHD-Liste ersetzt (Replace-
Strategie), analog zur NetApp-Discovery in netapp_discovery.py."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HyperVVm(Base):
    __tablename__ = "hyperv_vms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    host_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # VM-Konfiguration (siehe HyperVService.list_vms) -- fuer die VM-Details
    # im Inventory sowie als Quelle fuer die pro Backup-Lauf kopierte
    # BackupRunVmConfig (siehe app.models.backup_run).
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_startup_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_minimum_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_maximum_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dynamic_memory_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    network_adapters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pci_devices: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Aktuell vorhandene Checkpoints (Get-VMSnapshot) -- Liste von
    # {name, id, creation_time}. Grundlage fuer die Erkennung verwaister
    # Checkpoints (siehe run_alert_check in scheduler.py).
    checkpoints: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVVhd(Base):
    __tablename__ = "hyperv_vhds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    vm_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    path: Mapped[str] = mapped_column(String(1000))
    csv_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Gegenseitig exklusiv zu csv_name -- eine VHD liegt ENTWEDER auf einer
    # Cluster Shared Volume ODER direkt auf einem NetApp-CIFS-Export (SMB3,
    # Backlog #22), nie beides. Aus dem VHD-Pfad selbst geparst (siehe
    # _parse_smb_share in hyperv_clusters.py), kein zusaetzlicher WinRM-
    # Aufruf noetig -- anders als eine CSV ist ein SMB3-Share kein
    # Windows-Cluster-Ressourcenobjekt, nur ein UNC-Pfad in der VM-Konfig.
    smb_server: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smb_share: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Groesse der aufgeloesten Basis-VHDX, falls path eine aktive .avhdx ist
    # (siehe HyperVService._query_vms) -- Grundlage fuer die Inventory-
    # Anzeige "belegter Platz", die sonst bei einem aktiven Checkpoint die
    # kleine AVHDX-Differenzdatei zeigen wuerde. None = keine .avhdx oder
    # Basis nicht auflösbar.
    base_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVCsv(Base):
    """Cluster Shared Volumes je Hyper-V-Cluster. Die Zuordnung zum
    zugrunde liegenden NetApp-LUN/-Volume erfolgt beim Discovery-Lauf ueber
    den Vergleich der Windows-Disk-Seriennummer (Get-Disk) mit dem
    lun.serial_number-Feld der bereits registrierten NetApp-LUNs -- gegen
    echte Hardware verifiziert, dass beide Werte identisch sind. Wird keine
    passende LUN gefunden (z.B. NetApp-Cluster noch nicht registriert/
    discovered), bleiben die netapp_*-Felder leer."""

    __tablename__ = "hyperv_csvs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    owner_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    netapp_lun_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    netapp_lun_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    netapp_volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HyperVSmbShare(Base):
    """SMB3-Freigaben, auf denen Hyper-V-VMs direkt liegen (statt auf einer
    Cluster Shared Volume), Backlog #22. Anders als HyperVCsv gibt es dafuer
    KEIN eigenes Windows-Cluster-Ressourcenobjekt -- die Zeilen hier werden
    rein aus den bereits discoverten VHD-Pfaden abgeleitet (Gruppierung
    nach Server+Freigabename, siehe _refresh_smb_share_rows in
    hyperv_clusters.py). Die Zuordnung zum NetApp-Volume erfolgt ueber
    einen direkten Server+Freigabename-Abgleich gegen NetAppCifsShare/
    NetAppSvm.cifs_server_name -- kein Seriennummer-Umweg wie bei CSV/LUN
    noetig, da eine SMB3-Freigabe (anders als eine Block-LUN) ihren
    ONTAP-Namen direkt im UNC-Pfad traegt."""

    __tablename__ = "hyperv_smb_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cluster_id: Mapped[str] = mapped_column(String(36), ForeignKey("hyperv_clusters.id", ondelete="CASCADE"))
    server: Mapped[str] = mapped_column(String(255))
    share: Mapped[str] = mapped_column(String(255))
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    netapp_cifs_share_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    netapp_volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_svm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    netapp_cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
