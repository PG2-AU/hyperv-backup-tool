"""Auswertung der Standort-Kennzeichnung (siehe app.models.site): welcher
Standort gilt fuer einen Hyper-V-Knoten, eine CSV, eine SMB3-Freigabe, und
weicht der Host-Standort einer VM vom Standort ihres Storage ab?

Rein aus bereits discoverten DB-Daten abgeleitet (kein WinRM-/ONTAP-Aufruf)
-- gemeinsam genutzt von der Inventory-API (app.api.routes.vms), dem
Standort-Settings-Tab (app.api.routes.sites) und run_alert_check."""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.hyperv_discovery import HyperVCsv, HyperVSmbShare, HyperVVhd, HyperVVm
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppLun, NetAppSvm
from app.models.site import CsvSiteOverride, HyperVNodeSite, Site


def normalize_node_name(name: str | None) -> str:
    """Knotenname ohne DNS-Suffix, kleingeschrieben -- HyperVVm.host_name,
    Get-ClusterNode und CSV-Owner-Node liefern nicht garantiert dieselbe
    Schreibweise."""
    return (name or "").strip().split(".")[0].lower()


def _is_mcc_mirror_svm(svm: NetAppSvm) -> bool:
    # Gleiche Erkennung wie mcMirrorSvmNames in StoragePage.tsx: MetroCluster-
    # Sync-SVM auf dem Partner-Cluster, Namenskonvention '*-mc', nicht aktiv.
    return svm.name.endswith("-mc") and (svm.state or "").lower() != "running"


@dataclass
class VmSiteStatus:
    host_site: Site | None = None
    # Eindeutige, zugeordnete Standorte aller Disks der VM (CSV/SMB3).
    storage_sites: list[Site] = field(default_factory=list)
    # Mindestens eine Disk liegt auf Storage ohne Standort-Zuordnung.
    storage_unassigned: bool = False
    # Host-Standort bekannt UND mindestens ein Storage-Standort weicht ab.
    mismatch: bool = False
    # Namen der abweichenden CSVs/Freigaben (fuer Tooltip/Alarmtext).
    mismatched_storage: list[str] = field(default_factory=list)


class SiteResolver:
    """Laedt alle fuer die Standort-Aufloesung noetigen Zeilen einmalig und
    beantwortet danach beliebig viele Abfragen ohne weitere DB-Zugriffe."""

    def __init__(self, db: Session):
        self.sites: dict[str, Site] = {s.id: s for s in db.query(Site).all()}

        self._node_site: dict[tuple[str, str], str] = {
            (n.cluster_id, n.node_name): n.site_id for n in db.query(HyperVNodeSite).all()
        }
        self._override_site: dict[tuple[str, str], str] = {
            (o.cluster_id, o.disk_serial_number): o.site_id for o in db.query(CsvSiteOverride).all()
        }

        netapp_clusters = db.query(NetAppCluster).all()
        self._netapp_site_by_id: dict[str, str | None] = {c.id: c.site_id for c in netapp_clusters}
        # SMB3-Freigaben speichern nur den NetApp-Clusternamen (ontap_cluster_name
        # ODER Anzeigename, siehe _refresh_smb_share_rows) -- beide abbilden.
        self._netapp_site_by_name: dict[str, str | None] = {}
        for c in netapp_clusters:
            self._netapp_site_by_name[c.name] = c.site_id
            if c.ontap_cluster_name:
                self._netapp_site_by_name.setdefault(c.ontap_cluster_name, c.site_id)
        # Clusters, die gerade NICHT im MetroCluster-Normalbetrieb laufen.
        self.switchover_clusters: list[str] = sorted(
            c.name for c in netapp_clusters if c.metrocluster_mode and c.metrocluster_mode != "normal"
        )

        # LUN-Seriennummer -> NetApp-Cluster. Bei MetroCluster kann dieselbe
        # Seriennummer zusaetzlich auf der inaktiven '-mc'-Spiegel-SVM des
        # Partner-Clusters auftauchen -- die aktive SVM hat Vorrang, sonst
        # waere der Standort je nach Tabellenreihenfolge zufaellig.
        mirror_svms = {(s.cluster_id, s.name) for s in db.query(NetAppSvm).all() if _is_mcc_mirror_svm(s)}
        primary: dict[str, str] = {}
        mirror: dict[str, str] = {}
        for lun in db.query(NetAppLun).filter(NetAppLun.serial_number.isnot(None)).all():
            target = mirror if (lun.cluster_id, lun.svm_name) in mirror_svms else primary
            target.setdefault(lun.serial_number, lun.cluster_id)
        self._lun_cluster_by_serial: dict[str, str] = {**mirror, **primary}

        self._csvs: dict[tuple[str, str], HyperVCsv] = {
            (c.cluster_id, c.name.lower()): c for c in db.query(HyperVCsv).all()
        }
        self._smb_shares: dict[tuple[str, str, str], HyperVSmbShare] = {
            (s.cluster_id, s.server.lower(), s.share.lower()): s for s in db.query(HyperVSmbShare).all()
        }

    # --- Einzelobjekte ---------------------------------------------------

    def _site(self, site_id: str | None) -> Site | None:
        return self.sites.get(site_id) if site_id else None

    def node_site(self, cluster_id: str | None, node_name: str | None) -> Site | None:
        if not cluster_id or not node_name:
            return None
        return self._site(self._node_site.get((cluster_id, normalize_node_name(node_name))))

    def inherited_csv_site(self, csv: HyperVCsv) -> Site | None:
        """Vom NetApp-System der LUN geerbter Standort, ohne CSV-Override."""
        if not csv.disk_serial_number:
            return None
        netapp_cluster_id = self._lun_cluster_by_serial.get(csv.disk_serial_number)
        return self._site(self._netapp_site_by_id.get(netapp_cluster_id)) if netapp_cluster_id else None

    def csv_site(self, csv: HyperVCsv) -> tuple[Site | None, str | None]:
        """(Standort, Quelle) -- Quelle 'override' (manuell an der CSV) oder
        'netapp' (geerbt vom NetApp-System der LUN), None ohne Zuordnung."""
        if csv.disk_serial_number:
            override = self._site(self._override_site.get((csv.cluster_id, csv.disk_serial_number)))
            if override:
                return override, "override"
        inherited = self.inherited_csv_site(csv)
        return (inherited, "netapp") if inherited else (None, None)

    def csv_site_by_name(self, cluster_id: str, csv_name: str) -> Site | None:
        csv = self._csvs.get((cluster_id, csv_name.lower()))
        return self.csv_site(csv)[0] if csv else None

    def smb_share_site(self, share: HyperVSmbShare) -> Site | None:
        if not share.netapp_cluster_name:
            return None
        return self._site(self._netapp_site_by_name.get(share.netapp_cluster_name))

    # --- VM ------------------------------------------------------------------

    def vm_status(self, vm: HyperVVm, vhds: list[HyperVVhd]) -> VmSiteStatus:
        status = VmSiteStatus(host_site=self.node_site(vm.cluster_id, vm.host_name))
        storage: dict[str, Site] = {}
        mismatched: set[str] = set()
        for vhd in vhds:
            label: str | None = None
            site: Site | None = None
            if vhd.csv_name:
                label = vhd.csv_name
                site = self.csv_site_by_name(vhd.cluster_id, vhd.csv_name)
            elif vhd.smb_server and vhd.smb_share:
                label = f"\\\\{vhd.smb_server}\\{vhd.smb_share}"
                share = self._smb_shares.get((vhd.cluster_id, vhd.smb_server.lower(), vhd.smb_share.lower()))
                site = self.smb_share_site(share) if share else None
            else:
                continue  # lokale Disk o.ae. -- kein Storage-Standort ableitbar
            if site is None:
                status.storage_unassigned = True
                continue
            storage[site.id] = site
            if status.host_site and site.id != status.host_site.id:
                mismatched.add(label)
        status.storage_sites = sorted(storage.values(), key=lambda s: s.name)
        status.mismatched_storage = sorted(mismatched)
        status.mismatch = bool(mismatched)
        return status


def vhds_by_vm_uuid(db: Session) -> dict[str, list[HyperVVhd]]:
    result: dict[str, list[HyperVVhd]] = defaultdict(list)
    for vhd in db.query(HyperVVhd).filter(HyperVVhd.vm_uuid.isnot(None)).all():
        result[vhd.vm_uuid].append(vhd)
    return result
