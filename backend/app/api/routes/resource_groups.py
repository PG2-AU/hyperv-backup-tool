"""Protection Groups (intern weiterhin als ResourceGroup modelliert):
buendeln VMs oder CSVs zu einer benannten Gruppe (z.B. 'Bronze'), die mit
einer oder mehreren Backup-Policies verknuepft wird. Protection Group +
Policy zusammen ergeben die Backup-Definition."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from collections import defaultdict

from app.models.backup_policy import BackupPolicy, BackupScope
from app.models.hyperv_discovery import HyperVCsv, HyperVVhd
from app.models.netapp_discovery import NetAppSnapMirrorRelationship
from app.models.resource_group import ResourceGroup, parse_member_key
from app.schemas.resource_group import ResourceGroupRead, ResourceGroupWrite

router = APIRouter(prefix="/api/resource-groups", tags=["resource-groups"])


def _resolve_policies(policy_ids: list[str], db: Session) -> list[BackupPolicy]:
    if not policy_ids:
        return []
    policies = db.query(BackupPolicy).filter(BackupPolicy.id.in_(policy_ids)).all()
    found_ids = {p.id for p in policies}
    missing = set(policy_ids) - found_ids
    if missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Policy(s) nicht gefunden: {', '.join(missing)}")
    return policies


@router.get("", response_model=list[ResourceGroupRead])
def list_resource_groups(
    db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[ResourceGroup]:
    return db.query(ResourceGroup).order_by(ResourceGroup.name).all()


@router.post("", response_model=ResourceGroupRead, status_code=status.HTTP_201_CREATED)
def create_resource_group(
    payload: ResourceGroupWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> ResourceGroup:
    if db.query(ResourceGroup).filter(ResourceGroup.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Protection Group mit diesem Namen existiert bereits")

    group = ResourceGroup(
        name=payload.name,
        scope=payload.scope,
        members=payload.members,
        policies=_resolve_policies(payload.policy_ids, db),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.put("/{group_id}", response_model=ResourceGroupRead)
def update_resource_group(
    group_id: str,
    payload: ResourceGroupWrite,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.BACKUP_CREATE)),
) -> ResourceGroup:
    group = db.get(ResourceGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Protection Group nicht gefunden")

    duplicate = db.query(ResourceGroup).filter(ResourceGroup.name == payload.name, ResourceGroup.id != group_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Eine Protection Group mit diesem Namen existiert bereits")

    group.name = payload.name
    group.scope = payload.scope
    group.members = payload.members
    group.policies = _resolve_policies(payload.policy_ids, db)
    db.commit()
    db.refresh(group)
    return group


class SnapMirrorCheckGroup(BaseModel):
    scope: BackupScope
    members: list[str]


class SnapMirrorCheckRequest(BaseModel):
    groups: list[SnapMirrorCheckGroup]


class SnapMirrorCheckResult(BaseModel):
    svm_name: str
    volume_name: str
    members: list[str]
    has_relationship: bool
    policy_name: str | None = None
    destination_path: str | None = None


@router.post("/check-snapmirror", response_model=list[SnapMirrorCheckResult])
def check_snapmirror(
    payload: SnapMirrorCheckRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_VIEW)),
) -> list[SnapMirrorCheckResult]:
    """Loest die VM/CSV-Mitglieder einer oder mehrerer (noch nicht
    gespeicherter) Protection-Group-Auswahlen auf ihre zugrunde liegenden
    NetApp-Volumes auf und prueft, ob dafuer bereits eine SnapMirror-
    Beziehung discovert wurde (NetAppSnapMirrorRelationship, periodisch per
    Discovery befuellt -- kein Live-ONTAP-Aufruf noetig). Wird vom
    Policy-/Protection-Group-Formular aufgerufen, sobald eine Policy mit
    aktivem 'SnapMirror-Update nach Snapshot' auf ausgewaehlte Objekte
    angewendet werden soll (siehe SnapMirrorCheckPanel.tsx im Frontend)."""
    # (svm_name, volume_name) -> Menge der VM-/CSV-Namen, die darauf liegen
    volume_members: dict[tuple[str, str], set[str]] = {}

    for group in payload.groups:
        if not group.members:
            continue
        # (cluster_id, csv_name) -> Menge der urspruenglich ausgewaehlten
        # Namen (VM oder CSV), die tatsaechlich ueber dieses CSV auf dem
        # Volume liegen -- bei scope=vm wird das ueber die VHD-Zuordnung
        # aufgeloest, damit eine VM nicht faelschlich allen Volumes der
        # gesamten Gruppe zugerechnet wird, nur weil eine ANDERE VM der
        # Gruppe dort liegt. Cluster-qualifiziert (siehe
        # app.models.resource_group), damit ein CSV/eine VM mit identischem
        # Namen auf einem ANDEREN Cluster nicht faelschlich mit hineingezogen
        # wird -- ein noch nicht migrierter/mehrdeutiger Alt-Eintrag
        # (cluster_id is None) wird bewusst uebersprungen statt zu raten.
        csv_to_names: dict[tuple[str, str], set[str]] = {}
        if group.scope == BackupScope.CSV:
            for member in group.members:
                cluster_id, name = parse_member_key(member)
                if cluster_id is None:
                    continue
                csv_to_names.setdefault((cluster_id, name), set()).add(name)
        else:
            names_by_cluster: dict[str, set[str]] = defaultdict(set)
            for member in group.members:
                cluster_id, vm_name = parse_member_key(member)
                if cluster_id is not None:
                    names_by_cluster[cluster_id].add(vm_name)
            for cluster_id, vm_names in names_by_cluster.items():
                rows = (
                    db.query(HyperVVhd.vm_name, HyperVVhd.csv_name)
                    .filter(HyperVVhd.cluster_id == cluster_id, HyperVVhd.vm_name.in_(vm_names))
                    .distinct()
                    .all()
                )
                for row in rows:
                    if row.csv_name:
                        csv_to_names.setdefault((cluster_id, row.csv_name), set()).add(row.vm_name)

        if not csv_to_names:
            continue
        cluster_ids = {key[0] for key in csv_to_names}
        csvs = db.query(HyperVCsv).filter(HyperVCsv.cluster_id.in_(cluster_ids)).all()
        for csv in csvs:
            key = (csv.cluster_id, csv.name)
            names = csv_to_names.get(key)
            if names is None or not csv.netapp_svm_name or not csv.netapp_volume_name:
                continue
            vol_key = (csv.netapp_svm_name, csv.netapp_volume_name)
            volume_members.setdefault(vol_key, set()).update(names)

    if not volume_members:
        return []

    # Discovert-Beziehungen einmal laden statt pro Volume einzeln zu fragen.
    relationships = {
        rel.source_path: rel
        for rel in db.query(NetAppSnapMirrorRelationship).filter(NetAppSnapMirrorRelationship.source_path.isnot(None)).all()
    }

    results: list[SnapMirrorCheckResult] = []
    for (svm_name, volume_name), members in volume_members.items():
        rel = relationships.get(f"{svm_name}:{volume_name}")
        results.append(
            SnapMirrorCheckResult(
                svm_name=svm_name, volume_name=volume_name, members=sorted(members),
                has_relationship=rel is not None,
                policy_name=rel.policy_name if rel else None,
                destination_path=rel.destination_path if rel else None,
            )
        )
    return results


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource_group(
    group_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.BACKUP_DELETE)),
) -> None:
    group = db.get(ResourceGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Protection Group nicht gefunden")
    db.delete(group)
    db.commit()
