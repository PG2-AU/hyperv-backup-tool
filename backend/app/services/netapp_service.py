"""NetApp ONTAP Service: Snapshot- und SnapMirror-Operationen.

Kapselt den Zugriff auf die ONTAP REST-API (getestet gegen 9.18.1) ueber
das offizielle `netapp_ontap` SDK. MetroCluster-Cluster werden erkannt und
bei Snapshot-/SnapMirror-Operationen entsprechend beruecksichtigt (z.B.
Pruefung des MCC-Switchover-Status vor destruktiven Aktionen).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netapp_ontap import HostConnection
from netapp_ontap.error import NetAppRestError
from netapp_ontap.resources import Metrocluster, Snapshot, SnapmirrorRelationship, Volume

from app.core.config import Settings


@dataclass
class SnapshotInfo:
    uuid: str
    name: str
    volume_name: str
    create_time: str
    snapmirror_label: str | None = None


@dataclass
class SnapMirrorRelationshipInfo:
    uuid: str
    source_path: str
    destination_path: str
    state: str
    healthy: bool


@dataclass
class OperationResult:
    success: bool
    message: str = ""
    created_snapshot_uuids: list[str] = field(default_factory=list)


class NetAppOntapService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _connection(self) -> HostConnection:
        return HostConnection(
            self._settings.ontap_cluster_mgmt_lif,
            username=self._settings.ontap_username,
            password=self._settings.ontap_password,
            verify=self._settings.ontap_verify_ssl,
        )

    def is_metrocluster(self) -> bool:
        """Prueft, ob der angebundene Cluster Teil einer MetroCluster-Konfiguration ist."""
        with self._connection():
            try:
                mcc = Metrocluster()
                mcc.get()
                return getattr(mcc, "configuration_type", None) not in (None, "not_configured")
            except NetAppRestError:
                return False

    def metrocluster_switchover_in_progress(self) -> bool:
        with self._connection():
            try:
                mcc = Metrocluster()
                mcc.get()
                local = getattr(mcc, "local", None)
                return bool(local and getattr(local, "mode", "") not in ("normal", ""))
            except NetAppRestError:
                return False

    def create_snapshot(self, volume_name: str, svm_name: str, snapshot_name: str, snapmirror_label: str | None = None) -> SnapshotInfo:
        with self._connection():
            volume = Volume.find(name=volume_name, **{"svm.name": svm_name})
            if volume is None:
                raise ValueError(f"Volume '{volume_name}' auf SVM '{svm_name}' nicht gefunden")

            snapshot = Snapshot.from_dict(
                {
                    "name": snapshot_name,
                    "volume": {"uuid": volume.uuid},
                    "svm": {"name": svm_name},
                    **({"snapmirror_label": snapmirror_label} if snapmirror_label else {}),
                }
            )
            snapshot.post()
            snapshot.get()
            return SnapshotInfo(
                uuid=snapshot.uuid,
                name=snapshot.name,
                volume_name=volume_name,
                create_time=str(getattr(snapshot, "create_time", "")),
                snapmirror_label=snapmirror_label,
            )

    def delete_snapshot(self, volume_uuid: str, snapshot_uuid: str) -> OperationResult:
        """Wird u.a. beim automatischen Aufraeumen nach einem fehlgeschlagenen Backup verwendet."""
        with self._connection():
            try:
                snapshot = Snapshot(volume_uuid, uuid=snapshot_uuid)
                snapshot.delete()
                return OperationResult(success=True, message="Snapshot geloescht")
            except NetAppRestError as exc:
                return OperationResult(success=False, message=str(exc))

    def cleanup_snapshots(self, volume_uuid: str, snapshot_uuids: list[str]) -> OperationResult:
        """Best-effort Rollback: versucht alle uebergebenen Snapshots zu entfernen
        und meldet gesammelt, welche fehlgeschlagen sind, statt beim ersten Fehler abzubrechen."""
        failures: list[str] = []
        for snap_uuid in snapshot_uuids:
            result = self.delete_snapshot(volume_uuid, snap_uuid)
            if not result.success:
                failures.append(f"{snap_uuid}: {result.message}")

        if failures:
            return OperationResult(success=False, message="; ".join(failures))
        return OperationResult(success=True, message=f"{len(snapshot_uuids)} Snapshot(s) aufgeraeumt")

    def list_snapmirror_relationships(self, destination_svm: str | None = None) -> list[SnapMirrorRelationshipInfo]:
        with self._connection():
            query = {"destination.svm.name": destination_svm} if destination_svm else {}
            relationships = []
            for rel in SnapmirrorRelationship.get_collection(**query):
                rel.get()
                relationships.append(
                    SnapMirrorRelationshipInfo(
                        uuid=rel.uuid,
                        source_path=rel.source.path,
                        destination_path=rel.destination.path,
                        state=rel.state,
                        healthy=bool(getattr(rel, "healthy", False)),
                    )
                )
            return relationships

    def trigger_snapmirror_update(self, relationship_uuid: str) -> OperationResult:
        with self._connection():
            try:
                rel = SnapmirrorRelationship(uuid=relationship_uuid)
                rel.patch(hydrate=True, body={"state": "snapmirrored"})
                return OperationResult(success=True, message="SnapMirror-Update ausgeloest")
            except NetAppRestError as exc:
                return OperationResult(success=False, message=str(exc))
