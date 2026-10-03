"""Kontextbezogene Schnellsuche ueber VMs, Jobs und Storage-Objekte.

Das Frontend uebergibt optional `context` (z.B. "vms", "jobs", "storage"),
um die Suche auf den aktuell sichtbaren Bereich einzuschraenken.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_user_permissions
from app.db.session import get_db
from app.core.rbac import Permission
from app.models.backup_policy import BackupPolicy
from app.models.backup_run import BackupRunSnapshot
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVCsv, HyperVSmbShare, HyperVVm
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppCifsShare, NetAppLun, NetAppSnapMirrorRelationship, NetAppSvm, NetAppVolume
from app.models.resource_group import ResourceGroup

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchResult(BaseModel):
    type: str
    id: str
    label: str
    subtitle: str = ""
    route: str


@router.get("", response_model=list[SearchResult])
def search(
    q: str = Query(min_length=1),
    context: str | None = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SearchResult]:
    needle = q.lower()
    results: list[SearchResult] = []

    if context in (None, "netapp-clusters"):
        for cluster in db.query(NetAppCluster).all():
            if needle in cluster.name.lower() or needle in cluster.management_lif.lower():
                results.append(
                    SearchResult(
                        type="NetApp-Cluster",
                        id=cluster.id,
                        label=cluster.name,
                        subtitle=f"{cluster.management_lif} / {cluster.health.value}",
                        route="/storage?tab=clusters",
                    )
                )

    if context in (None, "vms"):
        for vm in db.query(HyperVVm).all():
            if needle in vm.name.lower():
                results.append(
                    SearchResult(
                        type="VM", id=vm.id, label=vm.name,
                        subtitle=f"{vm.host_name or '?'} / {vm.state or '?'}", route="/vms",
                    )
                )
        for csv in db.query(HyperVCsv).all():
            if needle in csv.name.lower():
                results.append(
                    SearchResult(
                        type="CSV", id=csv.id, label=csv.name, subtitle=csv.owner_node or "", route="/vms?tab=csv",
                    )
                )

    if context in (None, "jobs"):
        for policy in db.query(BackupPolicy).all():
            if needle in policy.name.lower():
                subtitle = f"{len(policy.resource_groups)} Protection Group(s)" if policy.resource_groups else "keine Protection Group"
                results.append(SearchResult(type="Policy", id=policy.id, label=policy.name, subtitle=subtitle, route="/jobs?tab=policies"))

    if context in (None, "resource-groups"):
        for group in db.query(ResourceGroup).all():
            if needle in group.name.lower():
                results.append(
                    SearchResult(
                        type="Protection Group",
                        id=group.id,
                        label=group.name,
                        subtitle=f"{group.scope.value} / {len(group.members)} Objekte",
                        route="/jobs?tab=protection-groups",
                    )
                )

    if context in (None, "storage"):
        for svm in db.query(NetAppSvm).all():
            if needle in svm.name.lower():
                results.append(SearchResult(type="SVM", id=svm.id, label=svm.name, subtitle=svm.state or "", route="/storage?tab=svms"))
        for rel in db.query(NetAppSnapMirrorRelationship).all():
            source = rel.source_path or ""
            destination = rel.destination_path or ""
            if needle in source.lower() or needle in destination.lower():
                results.append(SearchResult(type="SnapMirror", id=rel.id, label=source, subtitle=f"-> {destination}", route="/storage?tab=snapmirror"))

    return results[:20]


# --- Globale Suche in der Kopfzeile (Backlog #78, Nutzer-Vorgabe 2026-10-03) ---
# Suchfeld ueber VMs, CSVs, SMB3-/CIFS-Freigaben, Volumes, LUNs und die Backup-
# Snapshots der App. Vorgabe: maximal schnell, Treffer beim Tippen sofort --
# deshalb kein Server-Aufruf je Tastendruck: /index liefert einmal einen
# kompakten Index (Typ, Titel, Untertitel, Link), das Frontend filtert lokal
# (ohne Gross-/Kleinschreibung, '*'/'?' als Platzhalter). Jeder Link waehlt
# auf der Zielseite das Objekt aus bzw. belegt deren Tabellensuche vor. Typen
# ohne Leserecht des Benutzers fallen weg. Rein aus der DB (Discovery-Stand).


class SearchEntry(BaseModel):
    type: str
    title: str
    subtitle: str | None = None
    link: str


def _link(path: str, **params) -> str:
    return f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"


@router.get("/index", response_model=list[SearchEntry])
def search_index(db: Session = Depends(get_db), user=Depends(get_current_user)) -> list[SearchEntry]:
    """Kompakter Suchindex aller Objekte, die der Benutzer sehen darf. Das
    Frontend laedt ihn einmal (und frischt ihn im Hintergrund auf) und
    filtert bei jedem Tastendruck lokal -- Nutzer-Vorgabe: Treffer muessen
    sofort beim Tippen erscheinen, ohne Server-Roundtrip je Buchstabe."""
    perms = get_user_permissions(user, db)
    entries: list[SearchEntry] = []

    def join(*parts) -> str:
        return " · ".join(p for p in parts if p)

    if Permission.HYPERV_VIEW in perms:
        clusters = {c.id: c.name for c in db.query(HyperVCluster).all()}
        for v in db.query(HyperVVm).all():
            entries.append(SearchEntry(
                type="vm", title=v.name, subtitle=join(v.state, v.host_name, clusters.get(v.cluster_id)),
                link=_link("/vms", tab="vms", vm=v.id, q=v.name),
            ))
        for c in db.query(HyperVCsv).all():
            entries.append(SearchEntry(
                type="csv", title=c.name,
                subtitle=join(c.path, c.netapp_volume_name and f"Volume {c.netapp_volume_name}", clusters.get(c.cluster_id)),
                link=_link("/vms", tab="csv", csv=f"{c.cluster_id}::{c.name}", q=c.name),
            ))
        for sh in db.query(HyperVSmbShare).all():
            entries.append(SearchEntry(
                type="smb", title=f"\\\\{sh.server}\\{sh.share}",
                subtitle=join(sh.netapp_volume_name and f"Volume {sh.netapp_volume_name}", clusters.get(sh.cluster_id)),
                link=_link("/vms", tab="smb", smb=f"{sh.cluster_id}::{sh.server}::{sh.share}", q=sh.share),
            ))

    if Permission.STORAGE_VIEW in perms:
        systems = {c.id: c.name for c in db.query(NetAppCluster).all()}
        for v in db.query(NetAppVolume).all():
            entries.append(SearchEntry(
                type="volume", title=v.name, subtitle=join(v.svm_name and f"SVM {v.svm_name}", systems.get(v.cluster_id)),
                link=_link("/storage", tab="volumes", q=v.name),
            ))
        for lun in db.query(NetAppLun).all():
            short = lun.name.rsplit("/", 1)[-1]
            entries.append(SearchEntry(
                type="lun", title=short, subtitle=join(lun.name, lun.svm_name and f"SVM {lun.svm_name}", systems.get(lun.cluster_id)),
                link=_link("/storage", tab="luns", q=short),
            ))
        for sh in db.query(NetAppCifsShare).all():
            entries.append(SearchEntry(
                type="cifs", title=sh.name,
                subtitle=join(sh.svm_name and f"SVM {sh.svm_name}", sh.volume_name and f"Volume {sh.volume_name}", systems.get(sh.cluster_id)),
                link=_link("/storage", tab="cifs-shares", q=sh.name),
            ))

    if Permission.BACKUP_VIEW in perms and Permission.STORAGE_VIEW in perms:
        # Nur die Backup-Snapshots dieser App (fremde Snapshots kennt die DB
        # nicht) -- verlinkt auf das Volume, auf dem er noch liegt: primaer,
        # sonst die erste noch vorhandene SnapMirror-Kopie. selectinload statt
        # einer Abfrage je Zeile fuer die Ziel-Kopien.
        rows = (
            db.query(BackupRunSnapshot)
            .options(selectinload(BackupRunSnapshot.destinations))
            .filter(BackupRunSnapshot.snapshot_name.isnot(None))
            .order_by(BackupRunSnapshot.created_at.desc())
            .all()
        )
        for row in rows:
            if row.success and row.volume_uuid:
                where, volume_uuid = f"{row.svm_name}:{row.volume_name}", row.volume_uuid
            else:
                dest = next((d for d in row.destinations if d.present and d.destination_volume_uuid), None)
                if dest is None:
                    continue
                where, volume_uuid = f"{dest.destination_svm_name}:{dest.destination_volume_name} (sekundär)", dest.destination_volume_uuid
            covered = ", ".join(row.csv_names or []) or ", ".join((row.vm_names or [])[:3])
            entries.append(SearchEntry(
                type="snapshot", title=row.snapshot_name, subtitle=join(where, covered),
                link=_link("/storage", tab="volumes", snapshots=volume_uuid, q=row.snapshot_name),
            ))

    return entries
