"""NetApp ONTAP Service: Snapshot- und SnapMirror-Operationen sowie
Cluster-Verbindungsmanagement.

Kapselt den Zugriff auf die ONTAP REST-API (getestet gegen 9.18.1) ueber
das offizielle `netapp_ontap` SDK. MetroCluster-Cluster werden erkannt und
bei Snapshot-/SnapMirror-Operationen entsprechend beruecksichtigt (z.B.
Pruefung des MCC-Switchover-Status vor destruktiven Aktionen).

Ein Service-Objekt ist an genau eine Cluster-Verbindung gebunden (Host +
entweder Benutzername/Kennwort oder Client-Zertifikat), sodass mehrere
registrierte Cluster unabhaengig voneinander angesprochen werden koennen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from netapp_ontap import HostConnection
from netapp_ontap.error import NetAppRestError
from netapp_ontap.resources import (
    Account,
    Aggregate,
    Cluster,
    ClusterPeer,
    Igroup,
    IpInterface,
    Lun,
    LunMap,
    Metrocluster,
    Node,
    Schedule,
    SecurityCertificate,
    Snapshot,
    SnapmirrorPolicy,
    SnapmirrorRelationship,
    Svm,
    SvmPeer,
    Volume,
)


class NetAppConnectionError(Exception):
    """Verbindungsaufbau oder Authentifizierung gegen den Cluster fehlgeschlagen."""


def _get_nested(obj: object, path: str, default=None):
    """Sicherer verschachtelter Attributzugriff (z.B. 'svm.name'). Das
    netapp_ontap-SDK wirft bei nicht angefragten/nicht vorhandenen Feldern
    einen AttributeError statt None zurueckzugeben (siehe Vorfall mit
    Cluster.uuid) -- diese Funktion faengt das an jeder Stufe ab."""
    current = obj
    for part in path.split("."):
        try:
            current = getattr(current, part)
        except AttributeError:
            return default
        if current is None:
            return default
    return current


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
    lag_time: str | None = None
    last_transfer_size_bytes: int | None = None
    last_transfer_error: str | None = None
    schedule_name: str | None = None
    policy_name: str | None = None


@dataclass
class ClusterSummary:
    name: str
    uuid: str
    ontap_version: str
    node_count: int
    healthy_node_count: int
    healthy: bool
    is_metrocluster: bool


@dataclass
class DiscoveryStepResult:
    step: str
    success: bool
    message: str
    count: int | None = None


@dataclass
class DiscoveredSvm:
    uuid: str | None
    name: str
    state: str | None
    subtype: str | None
    allowed_protocols: str | None = None
    data_services: str | None = None


@dataclass
class DiscoveredVolume:
    uuid: str | None
    name: str
    svm_name: str | None
    state: str | None
    size_bytes: int | None
    used_bytes: int | None
    percent_used: int | None = None
    security_style: str | None = None
    language: str | None = None
    snapshot_autodelete_enabled: bool | None = None
    autosize_mode: str | None = None
    snapshot_policy_name: str | None = None
    encryption_enabled: bool | None = None
    snapmirror_protected: bool | None = None


@dataclass
class DiscoveredLun:
    uuid: str | None
    name: str
    svm_name: str | None
    volume_name: str | None
    state: str | None
    size_bytes: int | None
    os_type: str | None
    mapped_igroups: str | None = None


@dataclass
class DiscoveredIgroup:
    uuid: str | None
    name: str
    svm_name: str | None
    os_type: str | None
    protocol: str | None
    initiator_count: int = 0


@dataclass
class DiscoveredClusterPeer:
    uuid: str | None
    name: str | None
    remote_name: str | None
    state: str | None
    peer_ip_addresses: str | None = None
    local_ip_addresses: str | None = None


@dataclass
class DiscoveredSvmPeer:
    uuid: str | None
    svm_name: str | None
    peer_svm_name: str | None
    peer_cluster_name: str | None
    state: str | None
    applications: str | None = None


@dataclass
class DiscoveredNetworkInterface:
    uuid: str | None
    name: str | None
    address: str | None
    svm_name: str | None
    state: str | None


@dataclass
class DiscoveredPlatform:
    uuid: str | None
    node_name: str
    model: str | None
    serial_number: str | None
    ontap_version: str | None
    uptime_seconds: int | None
    state: str | None


@dataclass
class DiscoveredAggregate:
    uuid: str | None
    name: str
    node_name: str | None
    state: str | None
    size_bytes: int | None
    used_bytes: int | None
    used_percent: int | None = None
    efficiency_ratio: float | None = None
    efficiency_ratio_wo_snapshots: float | None = None


@dataclass
class DiscoveryData:
    svms: list[DiscoveredSvm] = field(default_factory=list)
    volumes: list[DiscoveredVolume] = field(default_factory=list)
    luns: list[DiscoveredLun] = field(default_factory=list)
    cluster_peers: list[DiscoveredClusterPeer] = field(default_factory=list)
    svm_peers: list[DiscoveredSvmPeer] = field(default_factory=list)
    snapmirror_relationships: list[SnapMirrorRelationshipInfo] = field(default_factory=list)
    network_interfaces: list[DiscoveredNetworkInterface] = field(default_factory=list)
    platforms: list[DiscoveredPlatform] = field(default_factory=list)
    aggregates: list[DiscoveredAggregate] = field(default_factory=list)
    igroups: list[DiscoveredIgroup] = field(default_factory=list)


@dataclass
class OperationResult:
    success: bool
    message: str = ""
    created_snapshot_uuids: list[str] = field(default_factory=list)


class NetAppOntapService:
    def __init__(
        self,
        *,
        host: str,
        verify_ssl: bool = True,
        username: str | None = None,
        password: str | None = None,
        cert_path: str | None = None,
        key_path: str | None = None,
    ):
        self._host = host
        self._verify_ssl = verify_ssl
        self._username = username
        self._password = password
        self._cert_path = cert_path
        self._key_path = key_path

    def _connection(self) -> HostConnection:
        if self._cert_path and self._key_path:
            return HostConnection(self._host, cert=self._cert_path, key=self._key_path, verify=self._verify_ssl)
        return HostConnection(self._host, username=self._username, password=self._password, verify=self._verify_ssl)

    def get_cluster_summary(self) -> ClusterSummary:
        """Verbindungstest + Basisinfo (Version, Node-Health, MetroCluster).
        Wird sowohl beim Hinzufuegen eines Clusters als auch fuer die
        regelmaessige Aktualisierung der 'NetApp-Systeme'-Uebersicht genutzt."""
        try:
            with self._connection():
                cluster = Cluster()
                cluster.get(fields="uuid,name,version")
                version = getattr(cluster, "version", None)
                version_str = getattr(version, "full", None) or "unbekannt"

                # Hinweis: 'health' ist als Wert fuer den 'fields'-Query-Parameter auf
                # manchen ONTAP-Versionen kein gueltiger Feldname (400 Bad Request).
                # 'state' ist das stabile, immer verfuegbare Basisfeld und genuegt hier.
                nodes = list(Node.get_collection(fields="state"))
                node_count = len(nodes)
                healthy_count = sum(1 for n in nodes if str(getattr(n, "state", "")) == "up")

                is_mcc = False
                try:
                    mcc = Metrocluster()
                    mcc.get()
                    is_mcc = getattr(mcc, "configuration_type", None) not in (None, "not_configured")
                except NetAppRestError:
                    is_mcc = False

                return ClusterSummary(
                    name=cluster.name,
                    uuid=cluster.uuid,
                    ontap_version=version_str,
                    node_count=node_count,
                    healthy_node_count=healthy_count,
                    healthy=node_count > 0 and healthy_count == node_count,
                    is_metrocluster=is_mcc,
                )
        except NetAppRestError as exc:
            raise NetAppConnectionError(str(exc)) from exc
        except Exception as exc:  # Transportfehler (Timeout, DNS, TLS, ...) sind keine NetAppRestError
            raise NetAppConnectionError(str(exc)) from exc

    def run_discovery(self) -> tuple[list[DiscoveryStepResult], DiscoveryData]:
        """Fuehrt eine mehrstufige Cluster-Discovery durch (Login, SVMs,
        Volumes, LUNs, Cluster-Peers, SVM-Peers, SnapMirror-Beziehungen,
        Netzwerk-Interfaces) und liefert sowohl die Schritt-Ergebnisse (fuer
        die Fortschrittsanzeige) als auch die tatsaechlich gefundenen Objekte
        (zur Persistierung) zurueck. Jeder Schritt wird einzeln abgesichert,
        damit z.B. eine fehlende SnapMirror-Lizenz nicht die gesamte Discovery
        abbricht -- der jeweilige Schritt wird dann nur als fehlgeschlagen
        markiert, die uebrigen Schritte laufen weiter.

        Feldabfragen nutzen 'fields=**' (ONTAP-Konvention fuer "alle Felder"),
        um genau die Art von 400-Fehlern zu vermeiden, die eine explizite,
        schmale Feldliste bei abweichenden ONTAP-Versionen ausloesen kann
        (siehe get_cluster_summary-Vorfall). Attributzugriffe erfolgen
        durchgehend ueber _get_nested/getattr mit Default, da das SDK bei
        fehlenden Feldern wirft statt None zu liefern."""
        results: list[DiscoveryStepResult] = []
        data = DiscoveryData()

        try:
            with self._connection():
                try:
                    cluster = Cluster()
                    cluster.get(fields="name")
                    cluster_name = _get_nested(cluster, "name", "unbekannt")
                    results.append(DiscoveryStepResult("login", True, f"Angemeldet an Cluster '{cluster_name}'"))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("login", False, str(exc)))
                    return results, data  # ohne erfolgreichen Login sind weitere Schritte zwecklos

                try:
                    svms = list(Svm.get_collection(fields="**"))
                    protocol_names = ("nfs", "cifs", "iscsi", "fcp", "nvme", "s3")
                    for s in svms:
                        allowed = [p for p in protocol_names if _get_nested(s, f"{p}.allowed")]
                        enabled = [p for p in protocol_names if _get_nested(s, f"{p}.enabled")]
                        data.svms.append(
                            DiscoveredSvm(
                                uuid=_get_nested(s, "uuid"),
                                name=_get_nested(s, "name", ""),
                                state=_get_nested(s, "state"),
                                subtype=_get_nested(s, "subtype"),
                                allowed_protocols=", ".join(allowed) or None,
                                data_services=", ".join(enabled) or None,
                            )
                        )
                    results.append(DiscoveryStepResult("svms", True, f"{len(svms)} Storage Virtual Machine(s) gefunden", len(svms)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("svms", False, str(exc)))

                try:
                    volumes = list(Volume.get_collection(fields="**"))
                    for v in volumes:
                        data.volumes.append(
                            DiscoveredVolume(
                                uuid=_get_nested(v, "uuid"),
                                name=_get_nested(v, "name", ""),
                                svm_name=_get_nested(v, "svm.name"),
                                state=_get_nested(v, "state"),
                                size_bytes=_get_nested(v, "space.size"),
                                used_bytes=_get_nested(v, "space.used"),
                                percent_used=_get_nested(v, "space.percent_used"),
                                security_style=_get_nested(v, "nas.security_style"),
                                language=_get_nested(v, "language"),
                                snapshot_autodelete_enabled=_get_nested(v, "space.snapshot.autodelete_enabled"),
                                autosize_mode=_get_nested(v, "autosize.mode"),
                                snapshot_policy_name=_get_nested(v, "snapshot_policy.name"),
                                encryption_enabled=_get_nested(v, "encryption.enabled"),
                                snapmirror_protected=_get_nested(v, "snapmirror.is_protected"),
                            )
                        )
                    results.append(DiscoveryStepResult("volumes", True, f"{len(volumes)} Volume(s) gefunden", len(volumes)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("volumes", False, str(exc)))

                try:
                    luns = list(Lun.get_collection(fields="**"))
                    for lun in luns:
                        data.luns.append(
                            DiscoveredLun(
                                uuid=_get_nested(lun, "uuid"),
                                name=_get_nested(lun, "name", ""),
                                svm_name=_get_nested(lun, "svm.name"),
                                volume_name=_get_nested(lun, "location.volume.name"),
                                state=_get_nested(lun, "status.state"),
                                size_bytes=_get_nested(lun, "space.size"),
                                os_type=_get_nested(lun, "os_type"),
                            )
                        )
                    results.append(DiscoveryStepResult("luns", True, f"{len(luns)} LUN(s) gefunden", len(luns)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("luns", False, str(exc)))

                try:
                    igroups = list(Igroup.get_collection(fields="**"))
                    for ig in igroups:
                        initiators = _get_nested(ig, "initiators") or []
                        data.igroups.append(
                            DiscoveredIgroup(
                                uuid=_get_nested(ig, "uuid"),
                                name=_get_nested(ig, "name", ""),
                                svm_name=_get_nested(ig, "svm.name"),
                                os_type=_get_nested(ig, "os_type"),
                                protocol=_get_nested(ig, "protocol"),
                                initiator_count=len(initiators),
                            )
                        )
                    results.append(DiscoveryStepResult("igroups", True, f"{len(igroups)} Initiator-Gruppe(n) gefunden", len(igroups)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("igroups", False, str(exc)))

                try:
                    lun_maps = list(LunMap.get_collection(fields="**"))
                    igroups_by_lun: dict[str, list[str]] = {}
                    for lm in lun_maps:
                        lun_name = _get_nested(lm, "lun.name")
                        igroup_name = _get_nested(lm, "igroup.name")
                        if lun_name and igroup_name:
                            igroups_by_lun.setdefault(lun_name, []).append(igroup_name)
                    for lun_obj in data.luns:
                        mapped = igroups_by_lun.get(lun_obj.name)
                        if mapped:
                            lun_obj.mapped_igroups = ", ".join(mapped)
                    results.append(DiscoveryStepResult("lun_maps", True, f"{len(lun_maps)} LUN-Mapping(s) gefunden", len(lun_maps)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("lun_maps", False, str(exc)))

                try:
                    local_intercluster_ips: list[str] = []
                    try:
                        local_lifs = IpInterface.get_collection(fields="ip.address", services="intercluster_core")
                        local_intercluster_ips = [ip for lif in local_lifs if (ip := _get_nested(lif, "ip.address"))]
                    except NetAppRestError:
                        pass  # lokale Intercluster-LIFs sind fuer die Peer-Liste selbst nicht kritisch
                    local_ip_str = ", ".join(local_intercluster_ips) or None

                    peers = list(ClusterPeer.get_collection(fields="**"))
                    for p in peers:
                        remote_ips = _get_nested(p, "remote.ip_addresses") or []
                        data.cluster_peers.append(
                            DiscoveredClusterPeer(
                                uuid=_get_nested(p, "uuid"),
                                name=_get_nested(p, "name"),
                                remote_name=_get_nested(p, "remote.name"),
                                state=_get_nested(p, "status.state"),
                                peer_ip_addresses=", ".join(remote_ips) or None,
                                local_ip_addresses=local_ip_str,
                            )
                        )
                    results.append(DiscoveryStepResult("cluster_peers", True, f"{len(peers)} Cluster-Peer-Beziehung(en) gefunden", len(peers)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("cluster_peers", False, str(exc)))

                try:
                    svm_peers = list(SvmPeer.get_collection(fields="**"))
                    for sp in svm_peers:
                        applications = _get_nested(sp, "applications") or []
                        data.svm_peers.append(
                            DiscoveredSvmPeer(
                                uuid=_get_nested(sp, "uuid"),
                                svm_name=_get_nested(sp, "svm.name"),
                                peer_svm_name=_get_nested(sp, "peer.svm.name"),
                                peer_cluster_name=_get_nested(sp, "peer.cluster.name"),
                                state=_get_nested(sp, "state"),
                                applications=", ".join(applications) or None,
                            )
                        )
                    results.append(DiscoveryStepResult("svm_peers", True, f"{len(svm_peers)} SVM-Peer-Beziehung(en) gefunden", len(svm_peers)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("svm_peers", False, str(exc)))

                try:
                    relationships = list(SnapmirrorRelationship.get_collection(fields="**"))
                    for rel in relationships:
                        unhealthy_reasons = _get_nested(rel, "unhealthy_reason") or []
                        last_error = None
                        if unhealthy_reasons:
                            try:
                                last_error = unhealthy_reasons[0].message
                            except AttributeError:
                                last_error = None
                        data.snapmirror_relationships.append(
                            SnapMirrorRelationshipInfo(
                                uuid=_get_nested(rel, "uuid", ""),
                                source_path=_get_nested(rel, "source.path", ""),
                                destination_path=_get_nested(rel, "destination.path", ""),
                                state=_get_nested(rel, "state", ""),
                                healthy=bool(_get_nested(rel, "healthy", False)),
                                lag_time=_get_nested(rel, "lag_time"),
                                last_transfer_size_bytes=_get_nested(rel, "transfer.bytes_transferred"),
                                last_transfer_error=last_error,
                                schedule_name=_get_nested(rel, "transfer_schedule.name"),
                                policy_name=_get_nested(rel, "policy.name"),
                            )
                        )
                    results.append(
                        DiscoveryStepResult("snapmirror", True, f"{len(relationships)} SnapMirror-Beziehung(en) gefunden", len(relationships))
                    )
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("snapmirror", False, str(exc)))

                try:
                    interfaces = list(IpInterface.get_collection(fields="**"))
                    for iface in interfaces:
                        data.network_interfaces.append(
                            DiscoveredNetworkInterface(
                                uuid=_get_nested(iface, "uuid"),
                                name=_get_nested(iface, "name"),
                                address=_get_nested(iface, "ip.address"),
                                svm_name=_get_nested(iface, "svm.name"),
                                state=_get_nested(iface, "state"),
                            )
                        )
                    results.append(
                        DiscoveryStepResult("network_interfaces", True, f"{len(interfaces)} Netzwerk-Interface(s) gefunden", len(interfaces))
                    )
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("network_interfaces", False, str(exc)))

                try:
                    nodes = list(Node.get_collection(fields="**"))
                    for n in nodes:
                        data.platforms.append(
                            DiscoveredPlatform(
                                uuid=_get_nested(n, "uuid"),
                                node_name=_get_nested(n, "name", ""),
                                model=_get_nested(n, "model"),
                                serial_number=_get_nested(n, "serial_number"),
                                ontap_version=_get_nested(n, "version.full"),
                                uptime_seconds=_get_nested(n, "uptime"),
                                state=_get_nested(n, "state"),
                            )
                        )
                    results.append(DiscoveryStepResult("platforms", True, f"{len(nodes)} Plattform(en) gefunden", len(nodes)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("platforms", False, str(exc)))

                try:
                    aggregates = list(Aggregate.get_collection(fields="**"))
                    for agg in aggregates:
                        data.aggregates.append(
                            DiscoveredAggregate(
                                uuid=_get_nested(agg, "uuid"),
                                name=_get_nested(agg, "name", ""),
                                node_name=_get_nested(agg, "node.name"),
                                state=_get_nested(agg, "state"),
                                size_bytes=_get_nested(agg, "space.block_storage.size"),
                                used_bytes=_get_nested(agg, "space.block_storage.used"),
                                used_percent=_get_nested(agg, "space.block_storage.used_percent"),
                                efficiency_ratio=_get_nested(agg, "space.efficiency.ratio"),
                                efficiency_ratio_wo_snapshots=_get_nested(agg, "space.efficiency_without_snapshots.ratio"),
                            )
                        )
                    results.append(DiscoveryStepResult("aggregates", True, f"{len(aggregates)} Aggregat(e) gefunden", len(aggregates)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("aggregates", False, str(exc)))
        except Exception as exc:  # Verbindungsfehler wie bei get_cluster_summary behandeln
            results.append(DiscoveryStepResult("login", False, str(exc)))

        return results, data

    def create_igroup(self, svm_name: str, name: str, os_type: str, protocol: str | None, initiators: list[str]) -> None:
        with self._connection():
            payload: dict = {"name": name, "svm": {"name": svm_name}, "os_type": os_type}
            if protocol:
                payload["protocol"] = protocol
            if initiators:
                payload["initiators"] = [{"name": i} for i in initiators]
            try:
                Igroup.from_dict(payload).post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Initiator-Gruppe konnte nicht angelegt werden: {exc}") from exc

    def create_volume(
        self, svm_name: str, name: str, aggregate_name: str, size_bytes: int,
        *, security_style: str | None = None, guarantee_type: str | None = None, volume_type: str | None = None,
    ) -> None:
        with self._connection():
            payload: dict = {"name": name, "svm": {"name": svm_name}, "aggregates": [{"name": aggregate_name}], "size": size_bytes}
            if security_style:
                payload["nas"] = {"security_style": security_style}
            if guarantee_type:
                payload["guarantee"] = {"type": guarantee_type}
            if volume_type:
                payload["type"] = volume_type
            try:
                Volume.from_dict(payload).post(poll=True, poll_timeout=120)
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Volume konnte nicht angelegt werden: {exc}") from exc

    def update_volume(self, uuid: str, *, size_bytes: int | None = None, state: str | None = None) -> None:
        # WICHTIG: from_dict(...).patch() sendet einen LEEREN Request-Body,
        # da das SDK Felder nur bei direkter Attribut-Zuweisung auf einem
        # (frisch instanziierten oder per .get() geladenen) Resource-Objekt
        # als "dirty" markiert -- ueber from_dict() gesetzte Felder werden
        # von .patch() nicht erkannt (gegen echte Hardware verifiziert: PATCH
        # mit from_dict() liefert 200 OK, aendert aber nichts). Deshalb hier
        # bewusst Attribut-Zuweisung statt from_dict() verwenden.
        with self._connection():
            volume = Volume(uuid=uuid)
            if size_bytes is not None:
                volume.space = {"size": size_bytes}
            if state is not None:
                volume.state = state
            try:
                volume.patch(poll=True, poll_timeout=120)
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Volume konnte nicht geändert werden: {exc}") from exc

    def delete_volume(self, uuid: str) -> None:
        with self._connection():
            try:
                Volume.from_dict({"uuid": uuid}).delete(poll=True, poll_timeout=120)
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Volume konnte nicht gelöscht werden: {exc}") from exc

    def create_lun(
        self, svm_name: str, volume_name: str, lun_name: str, os_type: str, size_bytes: int,
        *, space_allocation_enabled: bool = False,
    ) -> None:
        with self._connection():
            payload = {
                "name": f"/vol/{volume_name}/{lun_name}",
                "svm": {"name": svm_name},
                "os_type": os_type,
                "space": {"size": size_bytes, "scsi_thin_provisioning_support_enabled": space_allocation_enabled},
            }
            try:
                Lun.from_dict(payload).post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"LUN konnte nicht angelegt werden: {exc}") from exc

    def update_lun(self, uuid: str, *, size_bytes: int | None = None, enabled: bool | None = None) -> None:
        # Siehe Kommentar in update_volume: direkte Attribut-Zuweisung statt
        # from_dict(), sonst sendet .patch() einen leeren Body. Umbenennen wird
        # bewusst nicht unterstuetzt (der LUN-Name ist Teil des ONTAP-Pfads und
        # eine Aenderung waere eine Move-/Rename-Operation mit Nebenwirkungen
        # auf verbundene Hosts -- vom Nutzer explizit ausgeschlossen).
        with self._connection():
            lun = Lun(uuid=uuid)
            if size_bytes is not None:
                lun.space = {"size": size_bytes}
            if enabled is not None:
                lun.enabled = enabled
            try:
                lun.patch()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"LUN konnte nicht geändert werden: {exc}") from exc

    def delete_lun(self, uuid: str) -> None:
        with self._connection():
            try:
                Lun.from_dict({"uuid": uuid}).delete()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"LUN konnte nicht gelöscht werden: {exc}") from exc

    def delete_lun_map(self, lun_uuid: str, igroup_name: str, svm_name: str) -> None:
        with self._connection():
            try:
                igroup = Igroup.find(name=igroup_name, **{"svm.name": svm_name})
                if igroup is None:
                    raise NetAppConnectionError(f"Initiator-Gruppe '{igroup_name}' nicht gefunden")
                LunMap.from_dict({"lun": {"uuid": lun_uuid}, "igroup": {"uuid": igroup.uuid}}).delete()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"LUN-Mapping konnte nicht entfernt werden: {exc}") from exc

    def create_lun_map(self, svm_name: str, lun_name: str, igroup_name: str) -> None:
        with self._connection():
            payload = {"svm": {"name": svm_name}, "lun": {"name": lun_name}, "igroup": {"name": igroup_name}}
            try:
                LunMap.from_dict(payload).post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"LUN-Mapping konnte nicht angelegt werden: {exc}") from exc

    def list_snapmirror_policies(self) -> list[dict]:
        with self._connection():
            try:
                policies = SnapmirrorPolicy.get_collection(fields="name,type,svm.name")
                return [
                    {"name": _get_nested(p, "name"), "type": _get_nested(p, "type"), "svm_name": _get_nested(p, "svm.name")}
                    for p in policies
                ]
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SnapMirror-Policies konnten nicht abgerufen werden: {exc}") from exc

    def create_snapmirror_policy(self, svm_name: str, name: str, policy_type: str) -> None:
        with self._connection():
            try:
                SnapmirrorPolicy.from_dict({"name": name, "type": policy_type, "svm": {"name": svm_name}}).post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SnapMirror-Policy konnte nicht angelegt werden: {exc}") from exc

    def list_schedules(self) -> list[dict]:
        with self._connection():
            try:
                schedules = Schedule.get_collection(fields="name")
                return [{"name": _get_nested(s, "name")} for s in schedules]
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Schedules konnten nicht abgerufen werden: {exc}") from exc

    def create_schedule(self, name: str, preset: str) -> None:
        # SnapMirror akzeptiert nur cron-basierte Schedules, keine
        # Interval-Schedules (gegen echte Hardware verifiziert: "Schedule
        # ... is an interval schedule. SnapMirror does not support interval
        # schedules." bei 409 Conflict) -- daher hier bewusst ueber cron.minutes
        # abbilden statt der einfacheren 'interval'-Variante.
        cron: dict = {
            "every_5min": {"minutes": list(range(0, 60, 5))},
            "every_15min": {"minutes": list(range(0, 60, 15))},
            "every_30min": {"minutes": [0, 30]},
            "hourly": {"minutes": [0]},
            "daily": {"minutes": [0], "hours": [0]},
        }[preset]
        with self._connection():
            try:
                Schedule.from_dict({"name": name, "cron": cron}).post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Schedule konnte nicht angelegt werden: {exc}") from exc

    def create_snapmirror_relationship(
        self, source_path: str, destination_path: str, policy_name: str,
        schedule_name: str | None = None, source_cluster_name: str | None = None,
    ) -> str:
        with self._connection():
            source: dict = {"path": source_path}
            if source_cluster_name:
                source["cluster"] = {"name": source_cluster_name}
            payload: dict = {"source": source, "destination": {"path": destination_path}, "policy": {"name": policy_name}}
            if schedule_name:
                payload["transfer_schedule"] = {"name": schedule_name}
            try:
                rel = SnapmirrorRelationship.from_dict(payload)
                rel.post(poll=True, poll_timeout=120)
                rel.get(fields="uuid")
                return _get_nested(rel, "uuid", "")
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SnapMirror-Beziehung konnte nicht angelegt werden: {exc}") from exc

    def initialize_snapmirror_relationship(self, uuid: str) -> None:
        with self._connection():
            rel = SnapmirrorRelationship(uuid=uuid)
            rel.state = "snapmirrored"
            try:
                rel.patch(poll=True, poll_timeout=180)
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SnapMirror-Initialisierung fehlgeschlagen: {exc}") from exc

    def update_snapmirror_relationship(self, uuid: str, *, policy_name: str | None = None, schedule_name: str | None = None) -> None:
        # Direkte Attribut-Zuweisung statt from_dict() (siehe update_volume).
        # Ein leerer String bei schedule_name bedeutet "Schedule entfernen" --
        # ONTAP verlangt dafuer explizit {"uuid": null, "name": null} statt
        # eines leeren Namens (siehe REST-API-Doku zu transfer_schedule).
        with self._connection():
            rel = SnapmirrorRelationship(uuid=uuid)
            if policy_name is not None:
                rel.policy = {"name": policy_name}
            if schedule_name is not None:
                rel.transfer_schedule = {"uuid": None, "name": None} if schedule_name == "" else {"name": schedule_name}
            try:
                rel.patch(poll=True, poll_timeout=60)
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SnapMirror-Beziehung konnte nicht geändert werden: {exc}") from exc

    def generate_cluster_peer_passphrase(self) -> tuple[str, list[str]]:
        """Erzeugt eine Peering-Passphrase auf diesem Cluster (Schritt 1 des
        ONTAP-Cluster-Peering-Workflows, entspricht 'cluster peer create
        -generate-passphrase') und liefert sie zusammen mit den lokalen
        Intercluster-LIF-Adressen zurueck, die die Gegenseite fuer
        'remote.ip_addresses' benoetigt."""
        with self._connection():
            try:
                peer = ClusterPeer.from_dict({"authentication": {"generate_passphrase": True}})
                peer.post()
                peer.get(fields="authentication.passphrase")
                passphrase = _get_nested(peer, "authentication.passphrase")
                if not passphrase:
                    raise NetAppConnectionError("Keine Passphrase vom Cluster erhalten")
                local_lifs = IpInterface.get_collection(fields="ip.address", services="intercluster_core")
                local_ips = [ip for lif in local_lifs if (ip := _get_nested(lif, "ip.address"))]
                if not local_ips:
                    raise NetAppConnectionError("Keine Intercluster-LIFs auf diesem Cluster konfiguriert")
                return passphrase, local_ips
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Passphrase-Erzeugung fehlgeschlagen: {exc}") from exc

    def accept_cluster_peer(self, remote_ip_addresses: list[str], passphrase: str) -> None:
        """Schritt 2 des Cluster-Peering-Workflows: nimmt die von der
        Gegenseite erzeugte Passphrase an und stellt die Peer-Beziehung her."""
        with self._connection():
            try:
                peer = ClusterPeer.from_dict(
                    {"remote": {"ip_addresses": remote_ip_addresses}, "authentication": {"passphrase": passphrase}}
                )
                peer.post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"Cluster-Peering fehlgeschlagen: {exc}") from exc

    def create_svm_peer(self, local_svm_name: str, peer_cluster_name: str, peer_svm_name: str, applications: list[str]) -> None:
        with self._connection():
            try:
                peer = SvmPeer.from_dict(
                    {
                        "svm": {"name": local_svm_name},
                        "peer": {"cluster": {"name": peer_cluster_name}, "svm": {"name": peer_svm_name}},
                        "applications": applications,
                    }
                )
                peer.post()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SVM-Peering fehlgeschlagen: {exc}") from exc

    def accept_pending_svm_peer(self, local_svm_name: str, remote_svm_name: str) -> None:
        """Nimmt eine von der Gegenseite initiierte SVM-Peer-Anfrage an
        (PATCH state=peered). 'local_svm_name' ist die SVM auf DIESEM
        Cluster, 'remote_svm_name' die SVM auf der Gegenseite, die die
        Anfrage gestellt hat."""
        with self._connection():
            try:
                candidates = list(SvmPeer.get_collection(fields="uuid,state,svm.name,peer.svm.name"))
                match = next(
                    (
                        p
                        for p in candidates
                        if _get_nested(p, "svm.name") == local_svm_name
                        and _get_nested(p, "peer.svm.name") == remote_svm_name
                        and _get_nested(p, "state") == "pending"
                    ),
                    None,
                )
                if match is None:
                    raise NetAppConnectionError("Keine ausstehende SVM-Peer-Anfrage gefunden")
                match.state = "peered"
                match.patch()
            except NetAppRestError as exc:
                raise NetAppConnectionError(f"SVM-Peer-Annahme fehlgeschlagen: {exc}") from exc

    def install_client_certificate(self, common_name: str, cert_dir: Path, file_stem: str) -> tuple[str, str]:
        """Erzeugt ein selbstsigniertes Client-Zertifikat, installiert es auf
        dem Cluster als vertrauenswuerdige Client-CA und aktiviert
        zertifikatsbasierte Anmeldung fuer den angegebenen Benutzernamen. Der
        Common Name MUSS dem ONTAP-Benutzernamen entsprechen, da ONTAP darueber
        das Konto ermittelt. Muss ueber eine bestehende Kennwort-Verbindung
        aufgerufen werden (self._username/_password gesetzt).

        Hinweis: Das security/accounts-Payload orientiert sich an der
        NetApp-REST-API-Dokumentation, konnte mangels Zugriff auf eine echte
        ONTAP-9.18.1-Instanz aber nicht gegen echte Hardware verifiziert
        werden -- vor Produktiveinsatz gegen eine Testinstanz pruefen.
        """
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1095))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        try:
            with self._connection():
                ca_cert = SecurityCertificate.from_dict(
                    {"type": "client_ca", "common_name": common_name, "public_certificate": cert_pem.decode()}
                )
                ca_cert.post()

                account = Account.find(name=common_name)
                if account is None:
                    raise NetAppConnectionError(f"ONTAP-Benutzerkonto '{common_name}' nicht gefunden")
                account.get()
                applications = list(getattr(account, "applications", []) or [])
                applications.append({"application": "http", "authentication_methods": ["cert"]})
                account.applications = applications
                account.patch()
        except NetAppRestError as exc:
            raise NetAppConnectionError(f"Zertifikat-Installation fehlgeschlagen: {exc}") from exc

        cert_dir.mkdir(parents=True, exist_ok=True)
        cert_path = cert_dir / f"{file_stem}-client.crt"
        key_path = cert_dir / f"{file_stem}-client.key"
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)
        key_path.chmod(0o600)

        return str(cert_path), str(key_path)

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
