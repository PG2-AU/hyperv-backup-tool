"""Kontextbezogene Schnellsuche ueber VMs, Jobs und Storage-Objekte.

Das Frontend uebergibt optional `context` (z.B. "vms", "jobs", "storage"),
um die Suche auf den aktuell sichtbaren Bereich einzuschraenken.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes.vms import _DEMO_CSVS, _DEMO_VMS
from app.db.session import get_db
from app.models.backup_policy import BackupPolicy
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSnapMirrorRelationship, NetAppSvm
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
        for vm in _DEMO_VMS:
            if needle in vm.name.lower():
                results.append(SearchResult(type="VM", id=vm.id, label=vm.name, subtitle=f"{vm.host} / {vm.state}", route=f"/vms/{vm.id}"))
        for csv in _DEMO_CSVS:
            if needle in csv.name.lower():
                results.append(SearchResult(type="CSV", id=csv.name, label=csv.name, subtitle=csv.owner_node, route="/vms?tab=csv"))

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
