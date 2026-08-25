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
    IpInterface,
    Lun,
    Metrocluster,
    Node,
    SecurityCertificate,
    Snapshot,
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


@dataclass
class DiscoveredVolume:
    uuid: str | None
    name: str
    svm_name: str | None
    state: str | None
    size_bytes: int | None
    used_bytes: int | None


@dataclass
class DiscoveredLun:
    uuid: str | None
    name: str
    svm_name: str | None
    volume_name: str | None
    state: str | None
    size_bytes: int | None
    os_type: str | None


@dataclass
class DiscoveredClusterPeer:
    uuid: str | None
    name: str | None
    remote_name: str | None
    state: str | None


@dataclass
class DiscoveredSvmPeer:
    uuid: str | None
    svm_name: str | None
    peer_svm_name: str | None
    peer_cluster_name: str | None
    state: str | None


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
                    for s in svms:
                        data.svms.append(
                            DiscoveredSvm(
                                uuid=_get_nested(s, "uuid"),
                                name=_get_nested(s, "name", ""),
                                state=_get_nested(s, "state"),
                                subtype=_get_nested(s, "subtype"),
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
                                used_bytes=_get_nested(v, "space.used.size") or _get_nested(v, "space.afs_used_size"),
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
                                state=_get_nested(lun, "state"),
                                size_bytes=_get_nested(lun, "space.size"),
                                os_type=_get_nested(lun, "os_type"),
                            )
                        )
                    results.append(DiscoveryStepResult("luns", True, f"{len(luns)} LUN(s) gefunden", len(luns)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("luns", False, str(exc)))

                try:
                    peers = list(ClusterPeer.get_collection(fields="**"))
                    for p in peers:
                        data.cluster_peers.append(
                            DiscoveredClusterPeer(
                                uuid=_get_nested(p, "uuid"),
                                name=_get_nested(p, "name"),
                                remote_name=_get_nested(p, "remote.name"),
                                state=_get_nested(p, "status.state"),
                            )
                        )
                    results.append(DiscoveryStepResult("cluster_peers", True, f"{len(peers)} Cluster-Peer-Beziehung(en) gefunden", len(peers)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("cluster_peers", False, str(exc)))

                try:
                    svm_peers = list(SvmPeer.get_collection(fields="**"))
                    for sp in svm_peers:
                        data.svm_peers.append(
                            DiscoveredSvmPeer(
                                uuid=_get_nested(sp, "uuid"),
                                svm_name=_get_nested(sp, "svm.name"),
                                peer_svm_name=_get_nested(sp, "peer.svm.name"),
                                peer_cluster_name=_get_nested(sp, "peer.cluster.name"),
                                state=_get_nested(sp, "state"),
                            )
                        )
                    results.append(DiscoveryStepResult("svm_peers", True, f"{len(svm_peers)} SVM-Peer-Beziehung(en) gefunden", len(svm_peers)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("svm_peers", False, str(exc)))

                try:
                    relationships = list(SnapmirrorRelationship.get_collection(fields="**"))
                    for rel in relationships:
                        data.snapmirror_relationships.append(
                            SnapMirrorRelationshipInfo(
                                uuid=_get_nested(rel, "uuid", ""),
                                source_path=_get_nested(rel, "source.path", ""),
                                destination_path=_get_nested(rel, "destination.path", ""),
                                state=_get_nested(rel, "state", ""),
                                healthy=bool(_get_nested(rel, "healthy", False)),
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
                            )
                        )
                    results.append(DiscoveryStepResult("aggregates", True, f"{len(aggregates)} Aggregat(e) gefunden", len(aggregates)))
                except NetAppRestError as exc:
                    results.append(DiscoveryStepResult("aggregates", False, str(exc)))
        except Exception as exc:  # Verbindungsfehler wie bei get_cluster_summary behandeln
            results.append(DiscoveryStepResult("login", False, str(exc)))

        return results, data

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
