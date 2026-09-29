"""SMB3-Freigabe vergroessern (Nutzer-Anfrage 2026-09-29, Gegenstueck zu CSV
vergroessern in app.api.routes.csv_resize): Aktion in Inventory > SMB3-
Freigaben. Eine SMB3-Freigabe fuer Hyper-V liegt auf einem NetApp-Volume
(CIFS-Share), Windows sieht dessen Datenbereich direkt als Groesse der
Freigabe -- vergroessern heisst deshalb nur das Volume vergroessern, ohne
Rescan und ohne Partition auf der Hyper-V-Seite. Laeuft synchron (ein
einzelner ONTAP-PATCH), kein Hintergrund-Task. Nur Vergroessern."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.netapp_clusters import _log_storage_action
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.netapp_clusters import require_storage_unlocked
from app.db.session import get_db
from app.models.hyperv_discovery import HyperVSmbShare
from app.models.netapp_cluster import NetAppCluster, NetAppSystemType
from app.models.netapp_discovery import NetAppCifsShare, NetAppSvm, NetAppVolume
from app.services.netapp_service import NetAppConnectionError

router = APIRouter(prefix="/api/smb-resize", tags=["smb-resize"])


class SmbSharePart(BaseModel):
    server: str
    share: str
    path: str | None = None


class SmbVolumePart(BaseModel):
    uuid: str
    name: str
    svm_name: str | None = None
    size_bytes: int
    used_bytes: int | None = None
    available_bytes: int | None = None
    max_size_bytes: int | None = None
    snapshot_reserve_bytes: int | None = None
    snapshot_reserve_percent: int | None = None
    snapshot_used_bytes: int | None = None
    guarantee: str | None = None
    autosize_mode: str | None = None
    junction_path: str | None = None
    lun_count: int = 0


class SmbAggregatePart(BaseModel):
    name: str
    size_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None


class SmbResizeInfo(BaseModel):
    cluster_id: str
    netapp_cluster_id: str
    netapp_cluster_name: str
    share: SmbSharePart
    volume: SmbVolumePart
    aggregate: SmbAggregatePart | None = None
    aggregate_count: int = 1
    # Freigabe zeigt nicht auf die Wurzel des Volumes (Unterordner/Qtree) --
    # ein Qtree-Kontingent wuerde die sichtbare Groesse unabhaengig begrenzen.
    share_below_volume_root: bool = False


class SmbResizeRequest(BaseModel):
    cluster_id: str
    server: str
    share: str
    new_volume_size_bytes: int


class SmbResizeResult(BaseModel):
    volume_name: str
    size_before_bytes: int
    size_after_bytes: int


def _cifs_share_for(db: Session, server: str, share: str) -> NetAppCifsShare | None:
    """Server+Freigabename -> NetApp-CIFS-Share, wie in
    _refresh_smb_share_rows (hyperv_clusters.py): der CIFS-Servername haengt
    an der SVM, Vergleich ohne Gross-/Kleinschreibung."""
    cifs_server_by_svm = {
        (svm.cluster_id, svm.name): svm.cifs_server_name for svm in db.query(NetAppSvm).all() if svm.cifs_server_name
    }
    for candidate in db.query(NetAppCifsShare).filter(NetAppCifsShare.name.ilike(share)).all():
        cifs_server = cifs_server_by_svm.get((candidate.cluster_id, candidate.svm_name or ""))
        if cifs_server and cifs_server.lower() == server.lower() and candidate.name.lower() == share.lower():
            return candidate
    return None


def load_info(db: Session, cluster_id: str, server: str, share: str) -> SmbResizeInfo:
    row = (
        db.query(HyperVSmbShare)
        .filter(HyperVSmbShare.cluster_id == cluster_id, HyperVSmbShare.server == server, HyperVSmbShare.share == share)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SMB3-Freigabe nicht gefunden")
    cifs = _cifs_share_for(db, server, share)
    if cifs is None or not cifs.volume_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Die Freigabe ist keinem NetApp-Volume zugeordnet -- NetApp-System registriert und discovert?",
        )
    netapp_cluster = db.get(NetAppCluster, cifs.cluster_id)
    volume_row = (
        db.query(NetAppVolume)
        .filter(
            NetAppVolume.cluster_id == cifs.cluster_id, NetAppVolume.svm_name == cifs.svm_name,
            NetAppVolume.name == cifs.volume_name,
        )
        .first()
    )
    if netapp_cluster is None or volume_row is None or not volume_row.uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Volume '{cifs.volume_name}' ist nicht bekannt -- bitte Discovery ausführen.",
        )

    netapp = _netapp_service_for(netapp_cluster)
    try:
        volume = netapp.volume_space(volume_row.uuid)
        aggregate = None
        if netapp_cluster.system_type == NetAppSystemType.CLUSTER and volume["aggregate_names"]:
            aggregate = netapp.aggregate_space(volume["aggregate_names"][0])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NetApp-Abfrage fehlgeschlagen: {exc}") from exc

    junction = (volume["junction_path"] or "").rstrip("/")
    share_path = (cifs.path or "").replace("\\", "/").rstrip("/")
    return SmbResizeInfo(
        cluster_id=cluster_id, netapp_cluster_id=netapp_cluster.id, netapp_cluster_name=netapp_cluster.name,
        share=SmbSharePart(server=server, share=share, path=cifs.path),
        volume=SmbVolumePart(
            uuid=volume_row.uuid, name=volume["name"] or volume_row.name, svm_name=cifs.svm_name,
            size_bytes=volume["size_bytes"] or 0, used_bytes=volume["used_bytes"], available_bytes=volume["available_bytes"],
            max_size_bytes=volume["max_size_bytes"], snapshot_reserve_bytes=volume["snapshot_reserve_bytes"],
            snapshot_reserve_percent=volume["snapshot_reserve_percent"], snapshot_used_bytes=volume["snapshot_used_bytes"],
            guarantee=volume["guarantee"], autosize_mode=volume["autosize_mode"], junction_path=volume["junction_path"],
            lun_count=len(volume["luns"]),
        ),
        aggregate=SmbAggregatePart(**aggregate) if aggregate else None,
        aggregate_count=len(volume["aggregate_names"]) or 1,
        share_below_volume_root=bool(junction and share_path and share_path.lower() != junction.lower()),
    )


def validation_errors(info: SmbResizeInfo, new_size: int) -> list[str]:
    """Harte Grenzen -- identisch im Dialog. Leere Liste = zulaessig."""
    errors = []
    if new_size <= info.volume.size_bytes:
        errors.append("Die neue Größe muss größer sein als die bisherige -- Verkleinern wird nicht unterstützt.")
    if info.volume.max_size_bytes and new_size > info.volume.max_size_bytes:
        errors.append("Die neue Volume-Größe überschreitet die maximale Volume-Größe dieses Systems.")
    if (
        info.aggregate and info.aggregate.available_bytes is not None and info.aggregate_count == 1
        and info.volume.guarantee == "volume" and new_size - info.volume.size_bytes > info.aggregate.available_bytes
    ):
        errors.append("Im Aggregat ist nicht genug freier Platz für die Volume-Vergrößerung.")
    return errors


@router.get("/{cluster_id}", response_model=SmbResizeInfo)
def get_info(
    cluster_id: str, server: str, share: str, db: Session = Depends(get_db), user=Depends(require_storage_unlocked),
) -> SmbResizeInfo:
    return load_info(db, cluster_id, server, share)


@router.post("", response_model=SmbResizeResult)
def resize(payload: SmbResizeRequest, db: Session = Depends(get_db), user=Depends(require_storage_unlocked)) -> SmbResizeResult:
    info = load_info(db, payload.cluster_id, payload.server, payload.share)
    errors = validation_errors(info, payload.new_volume_size_bytes)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))
    netapp = _netapp_service_for(db.get(NetAppCluster, info.netapp_cluster_id))
    try:
        netapp.update_volume(info.volume.uuid, size_bytes=payload.new_volume_size_bytes)
        after = netapp.volume_space(info.volume.uuid)
    except NetAppConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    size_after = after["size_bytes"] or payload.new_volume_size_bytes
    # Inventory sofort nachziehen statt bis zur naechsten Discovery zu warten:
    # HyperVSmbShare.capacity_bytes kommt ohnehin vom Volume (siehe
    # _refresh_smb_share_rows), dasselbe Volume kann mehrere Freigaben tragen.
    now = datetime.now(timezone.utc)
    volume_row = db.query(NetAppVolume).filter(NetAppVolume.uuid == info.volume.uuid).first()
    if volume_row is not None:
        volume_row.size_bytes = size_after
    for row in db.query(HyperVSmbShare).filter(
        HyperVSmbShare.netapp_volume_name == info.volume.name, HyperVSmbShare.netapp_svm_name == info.volume.svm_name,
    ):
        row.capacity_bytes = size_after
        row.last_seen_at = now
    db.commit()
    _log_storage_action(
        db, user,
        f"SMB3-Freigabe '\\\\{payload.server}\\{payload.share}' vergrößert: Volume '{info.volume.name}' "
        f"{info.volume.size_bytes / 1024**3:.1f} GB → {size_after / 1024**3:.1f} GB",
    )
    return SmbResizeResult(volume_name=info.volume.name, size_before_bytes=info.volume.size_bytes, size_after_bytes=size_after)
