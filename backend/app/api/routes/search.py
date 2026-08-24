"""Kontextbezogene Schnellsuche ueber VMs, Jobs und Storage-Objekte.

Das Frontend uebergibt optional `context` (z.B. "vms", "jobs", "storage"),
um die Suche auf den aktuell sichtbaren Bereich einzuschraenken.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes.storage import _DEMO_RELATIONSHIPS, _DEMO_SVMS
from app.api.routes.vms import _DEMO_CSVS, _DEMO_VMS
from app.db.session import get_db
from app.models.backup_job import BackupJob
from app.models.netapp_cluster import NetAppCluster

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
                        route="/netapp-clusters",
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
        for job in db.query(BackupJob).all():
            if needle in job.name.lower():
                subtitle = job.scope.value if job.scope else "kein Scope"
                results.append(SearchResult(type="Job", id=job.id, label=job.name, subtitle=subtitle, route=f"/jobs/{job.id}"))

    if context in (None, "storage"):
        for svm in _DEMO_SVMS:
            if needle in svm.name.lower():
                results.append(SearchResult(type="SVM", id=svm.name, label=svm.name, subtitle=svm.state, route="/storage?tab=svms"))
        for rel in _DEMO_RELATIONSHIPS:
            if needle in rel.source_path.lower() or needle in rel.destination_path.lower():
                results.append(SearchResult(type="SnapMirror", id=rel.uuid, label=rel.source_path, subtitle=f"-> {rel.destination_path}", route="/storage?tab=snapmirror"))

    return results[:20]
