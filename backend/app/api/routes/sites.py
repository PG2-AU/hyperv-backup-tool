"""Standorte (Rechenzentren) und ihre Zuordnung zu Hyper-V-Knoten, NetApp-
Systemen und einzelnen CSVs -- Settings > Standorte. Hintergrund und
Datenmodell siehe app.models.site, Auswertung siehe app.core.sites.

Lesen mit HYPERV_VIEW (die Inventory-Ansicht braucht die Standortliste fuer
Filter/Badges), Aendern nur mit SETTINGS_MANAGE."""

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.core.sites import SiteResolver, normalize_node_name, vhds_by_vm_uuid
from app.db.session import get_db
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVVm
from app.models.netapp_cluster import NetAppCluster
from app.models.site import CsvSiteOverride, HyperVNodeSite, Site
from app.models.system_log import SystemLogEvent
from app.models.user import User
from app.schemas.site import (
    CsvAssignmentRead,
    CsvOverrideWrite,
    HyperVClusterAssignmentRead,
    NetAppAssignmentWrite,
    NetAppClusterAssignmentRead,
    NodeAssignmentRead,
    NodeAssignmentWrite,
    SiteAssignmentsRead,
    SiteMismatchSummary,
    SiteRead,
    SiteWrite,
)

router = APIRouter(prefix="/api/sites", tags=["sites"])


def _log_site_action(db: Session, user: User, message: str) -> None:
    actor = user.display_name or user.username
    db.add(SystemLogEvent(level="INFO", source="sites", message=f"{message} (durch {actor})"))
    db.commit()


def _get_site_or_404(db: Session, site_id: str) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Standort nicht gefunden")
    return site


def _site_name(db: Session, site_id: str | None) -> str:
    return _get_site_or_404(db, site_id).name if site_id else "(keiner)"


# --- Standorte ---------------------------------------------------------------


@router.get("", response_model=list[SiteRead])
def list_sites(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[Site]:
    return db.query(Site).order_by(Site.name).all()


@router.post("", response_model=SiteRead, status_code=status.HTTP_201_CREATED)
def create_site(
    payload: SiteWrite, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> Site:
    name = payload.name.strip()
    if db.query(Site).filter(Site.name == name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Standort '{name}' existiert bereits")
    site = Site(name=name, description=(payload.description or "").strip() or None, color=payload.color)
    db.add(site)
    db.commit()
    db.refresh(site)
    _log_site_action(db, user, f"Standort '{site.name}' angelegt")
    return site


@router.put("/{site_id}", response_model=SiteRead)
def update_site(
    site_id: str, payload: SiteWrite, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> Site:
    site = _get_site_or_404(db, site_id)
    name = payload.name.strip()
    duplicate = db.query(Site).filter(Site.name == name, Site.id != site_id).first()
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Standort '{name}' existiert bereits")
    site.name = name
    site.description = (payload.description or "").strip() or None
    site.color = payload.color
    db.commit()
    db.refresh(site)
    _log_site_action(db, user, f"Standort '{site.name}' bearbeitet")
    return site


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(
    site_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> None:
    site = _get_site_or_404(db, site_id)
    # Verweise selbst aufraeumen -- SQLite erzwingt ondelete=CASCADE hier
    # nicht (kein PRAGMA foreign_keys), NetAppCluster.site_id hat ohnehin
    # keinen FK-Constraint (siehe app.models.netapp_cluster).
    db.query(HyperVNodeSite).filter(HyperVNodeSite.site_id == site_id).delete()
    db.query(CsvSiteOverride).filter(CsvSiteOverride.site_id == site_id).delete()
    db.query(NetAppCluster).filter(NetAppCluster.site_id == site_id).update({NetAppCluster.site_id: None})
    name = site.name
    db.delete(site)
    db.commit()
    _log_site_action(db, user, f"Standort '{name}' gelöscht (inkl. aller Zuordnungen)")


# --- Zuordnungen -------------------------------------------------------------


@router.get("/assignments", response_model=SiteAssignmentsRead)
def get_assignments(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW)),
) -> SiteAssignmentsRead:
    resolver = SiteResolver(db)
    node_sites = {(n.cluster_id, n.node_name): n.site_id for n in db.query(HyperVNodeSite).all()}
    overrides = {(o.cluster_id, o.disk_serial_number): o.site_id for o in db.query(CsvSiteOverride).all()}

    vms = db.query(HyperVVm).all()
    vm_counts = Counter((vm.cluster_id, normalize_node_name(vm.host_name)) for vm in vms if vm.host_name)
    csvs = db.query(HyperVCsv).order_by(HyperVCsv.name).all()

    hyperv_clusters: list[HyperVClusterAssignmentRead] = []
    clusters = db.query(HyperVCluster).order_by(HyperVCluster.name).all()
    cluster_names = {c.id: c.name for c in clusters}
    for cluster in clusters:
        # Knoten aus allen verfuegbaren Quellen zusammentragen -- Get-ClusterNode
        # (letzter Health-Check), VM-Hosts, CSV-Owner und bereits gespeicherte
        # Zuordnungen (falls ein Knoten gerade fehlt, soll seine Zuordnung
        # trotzdem sichtbar/entfernbar bleiben). Anzeigename = erste
        # gesehene Originalschreibweise ohne DNS-Suffix.
        display: dict[str, str] = {}
        sources = (
            list(cluster.node_names)
            + [vm.host_name for vm in vms if vm.cluster_id == cluster.id and vm.host_name]
            + [c.owner_node for c in csvs if c.cluster_id == cluster.id and c.owner_node]
            + [name for (cid, name) in node_sites if cid == cluster.id]
        )
        for raw in sources:
            key = normalize_node_name(raw)
            if key:
                display.setdefault(key, raw.strip().split(".")[0])
        nodes = [
            NodeAssignmentRead(
                node_name=display[key],
                site_id=node_sites.get((cluster.id, key)),
                vm_count=vm_counts.get((cluster.id, key), 0),
            )
            for key in sorted(display)
        ]
        hyperv_clusters.append(HyperVClusterAssignmentRead(cluster_id=cluster.id, cluster_name=cluster.name, nodes=nodes))

    netapp_clusters = [
        NetAppClusterAssignmentRead(
            netapp_cluster_id=c.id, name=c.name, is_metrocluster=c.is_metrocluster,
            metrocluster_mode=c.metrocluster_mode, site_id=c.site_id,
        )
        for c in db.query(NetAppCluster).order_by(NetAppCluster.name).all()
    ]

    csv_rows: list[CsvAssignmentRead] = []
    for csv in csvs:
        override_id = overrides.get((csv.cluster_id, csv.disk_serial_number)) if csv.disk_serial_number else None
        inherited = resolver.inherited_csv_site(csv)
        csv_rows.append(
            CsvAssignmentRead(
                cluster_id=csv.cluster_id, cluster_name=cluster_names.get(csv.cluster_id, "?"),
                csv_name=csv.name, disk_serial_number=csv.disk_serial_number,
                netapp_cluster_name=csv.netapp_cluster_name,
                inherited_site_id=inherited.id if inherited else None, override_site_id=override_id,
            )
        )

    return SiteAssignmentsRead(
        hyperv_clusters=hyperv_clusters, netapp_clusters=netapp_clusters, csvs=csv_rows,
        switchover_clusters=resolver.switchover_clusters,
    )


@router.put("/assignments/node", status_code=status.HTTP_204_NO_CONTENT)
def set_node_assignment(
    payload: NodeAssignmentWrite, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> None:
    cluster = db.get(HyperVCluster, payload.cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    site_name = _site_name(db, payload.site_id)
    key = normalize_node_name(payload.node_name)
    existing = (
        db.query(HyperVNodeSite)
        .filter(HyperVNodeSite.cluster_id == payload.cluster_id, HyperVNodeSite.node_name == key)
        .first()
    )
    if payload.site_id is None:
        if existing:
            db.delete(existing)
    elif existing:
        existing.site_id = payload.site_id
    else:
        db.add(HyperVNodeSite(cluster_id=payload.cluster_id, node_name=key, site_id=payload.site_id))
    db.commit()
    _log_site_action(db, user, f"Standort von Hyper-V-Knoten '{payload.node_name}' ({cluster.name}) auf {site_name} gesetzt")


@router.put("/assignments/netapp", status_code=status.HTTP_204_NO_CONTENT)
def set_netapp_assignment(
    payload: NetAppAssignmentWrite, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> None:
    cluster = db.get(NetAppCluster, payload.netapp_cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetApp-System nicht gefunden")
    site_name = _site_name(db, payload.site_id)
    cluster.site_id = payload.site_id
    db.commit()
    _log_site_action(db, user, f"Standort von NetApp-System '{cluster.name}' auf {site_name} gesetzt")


@router.put("/assignments/csv", status_code=status.HTTP_204_NO_CONTENT)
def set_csv_override(
    payload: CsvOverrideWrite, db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.SETTINGS_MANAGE)),
) -> None:
    if db.get(HyperVCluster, payload.cluster_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    site_name = _site_name(db, payload.site_id)
    existing = (
        db.query(CsvSiteOverride)
        .filter(
            CsvSiteOverride.cluster_id == payload.cluster_id,
            CsvSiteOverride.disk_serial_number == payload.disk_serial_number,
        )
        .first()
    )
    if payload.site_id is None:
        if existing:
            db.delete(existing)
    elif existing:
        existing.site_id = payload.site_id
        existing.csv_name = payload.csv_name
    else:
        db.add(
            CsvSiteOverride(
                cluster_id=payload.cluster_id, disk_serial_number=payload.disk_serial_number,
                csv_name=payload.csv_name, site_id=payload.site_id,
            )
        )
    db.commit()
    label = payload.csv_name or payload.disk_serial_number
    if payload.site_id is None:
        _log_site_action(db, user, f"Standort-Override von CSV '{label}' entfernt (erbt wieder vom NetApp-System)")
    else:
        _log_site_action(db, user, f"Standort von CSV '{label}' manuell auf {site_name} gesetzt")


# --- Dashboard -----------------------------------------------------------------


@router.get("/mismatch-summary", response_model=SiteMismatchSummary)
def mismatch_summary(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW)),
) -> SiteMismatchSummary:
    resolver = SiteResolver(db)
    if not resolver.sites:
        return SiteMismatchSummary(sites_configured=False, mismatch_count=0, unassigned_count=0)
    vhds = vhds_by_vm_uuid(db)
    mismatch = unassigned = 0
    for vm in db.query(HyperVVm).all():
        st = resolver.vm_status(vm, vhds.get(vm.vm_uuid, []) if vm.vm_uuid else [])
        if st.mismatch:
            mismatch += 1
        if st.host_site is None or st.storage_unassigned:
            unassigned += 1
    return SiteMismatchSummary(
        sites_configured=True, mismatch_count=mismatch, unassigned_count=unassigned,
        switchover_clusters=resolver.switchover_clusters,
    )

