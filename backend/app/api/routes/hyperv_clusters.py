"""Hyper-V-Failover-Cluster-Verwaltung: Hinzufuegen/Entfernen registrierter
Cluster sowie Verbindungstest per WinRM (siehe HyperVService.get_cluster_summary).

Registriert wird der Cluster (Cluster Name Object / Management-IP), nicht die
einzelnen Knoten -- vgl. NetApp-Cluster-Verwaltung in netapp_clusters.py.
"""

import json
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.backup_run import BackupRun, JobStatus
from app.models.hyperv_cluster import HyperVCluster, HyperVClusterHealth
from app.models.hyperv_discovery import HyperVCsv, HyperVSmbShare, HyperVVhd, HyperVVm
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppCifsShare, NetAppLun, NetAppSvm, NetAppVolume
from app.schemas.hyperv_cluster import HyperVClusterCreate, HyperVClusterRead, HyperVClusterUpdate, HyperVReachabilityCheck
from app.schemas.netapp_cluster import DiscoveryStepRead
from app.services.hyperv_service import (
    ClusterSharedVolumeInfo,
    HyperVConnectionError,
    HyperVService,
    VirtualMachineInfo,
    check_reachability,
)

router = APIRouter(prefix="/api/hyperv/clusters", tags=["hyperv-clusters"])

_CSV_NAME_RE = re.compile(r"ClusterStorage\\([^\\]+)\\", re.IGNORECASE)


def _parse_csv_name(vhd_path: str) -> str | None:
    match = _CSV_NAME_RE.search(vhd_path)
    return match.group(1) if match else None


# Backlog #22: eine VHD auf einem NetApp-CIFS-Export liegt als UNC-Pfad vor
# (z.B. '\\DEMO7\vol_hv1_smb3\VM01\VM01.vhdx', live verifiziert) statt unter
# 'ClusterStorage\'. Server + Freigabename sind die ersten zwei
# Pfadsegmente.
_SMB_PATH_RE = re.compile(r"^\\\\([^\\]+)\\([^\\]+)\\")


def _parse_smb_share(vhd_path: str) -> tuple[str, str] | None:
    match = _SMB_PATH_RE.match(vhd_path)
    return (match.group(1), match.group(2)) if match else None


def _folder_name_from_csv_path(csv_path: str | None) -> str | None:
    """Letztes Pfadsegment aus dem CSV-Mount-Pfad (z.B.
    'C:\\ClusterStorage\\Volume20' -> 'Volume20')."""
    if not csv_path:
        return None
    return csv_path.rstrip("\\/").rsplit("\\", 1)[-1] or None


def _resolve_csv_name(folder_name: str | None, csvs) -> str | None:
    """Loest den von _parse_csv_name() aus einem VHD-Pfad ermittelten
    MOUNT-ORDNERNAMEN (z.B. 'Volume20') zum tatsaechlichen CSV-
    Ressourcennamen auf (z.B. 'CSV03'). Live gefunden: Windows unterscheidet
    zwischen dem CSV-Ressourcennamen (frei umbenennbar in Failover Cluster
    Manager, taucht als HyperVCsv.name/Get-ClusterSharedVolume.Name auf)
    und dem Mount-Ordnernamen unter C:\\ClusterStorage\\ (bleibt beim
    Umbenennen der CSV ueblicherweise unveraendert) -- landete bislang der
    rohe Ordnername direkt in HyperVVhd.csv_name, schlug JEDER Vergleich
    gegen HyperVCsv.name fehl, sobald eine CSV umbenannt wurde (in der
    Praxis haeufig): Speicherkette in der GUI zeigte 'CSV-Details nicht
    verfuegbar' fuer ausnahmslos jede VM, CSV-/VM-scope Resource Groups
    fanden keine Ziele, und die Restore-Live-Aufloesung schlug fehl.
    `csvs` ist ein Iterable von Objekten mit .name und .path bzw.
    .volume_path (akzeptiert sowohl HyperVCsv-Zeilen als auch
    ClusterSharedVolumeInfo aus einer laufenden Discovery). Kein Treffer
    (z.B. CSV-Discovery lief noch nie) -> der Ordnername selbst als
    Fallback, wie im bisherigen (fehlerhaften) Verhalten."""
    if not folder_name:
        return None
    # Live gefunden (2026-09-15, Produktivumgebung): Get-VMHardDiskDrive
    # kann den Mount-Ordner in abweichender Gross-/Kleinschreibung liefern
    # (z.B. 'volume16'), obwohl der tatsaechliche CSV-Ordner 'Volume16'
    # heisst -- Windows-Pfade sind case-insensitiv, ein reiner Python-'=='-
    # Vergleich ist es aber nicht. Ohne .lower() schlug der Vergleich fehl,
    # die betroffene VM landete nie in HyperVVhd.csv_name mit dem echten
    # CSV-Namen und wurde dadurch faelschlich als "ungeschuetzt" gefuehrt,
    # obwohl ihr CSV einer Protection Group zugeordnet war.
    folder_name_lower = folder_name.lower()
    for csv in csvs:
        path = getattr(csv, "path", None) or getattr(csv, "volume_path", None)
        csv_folder = _folder_name_from_csv_path(path)
        if csv_folder is not None and csv_folder.lower() == folder_name_lower:
            return csv.name
    return folder_name


def _resolve_vhd_location(vhd_path: str, csvs) -> tuple[str | None, str | None, str | None]:
    """Loest den Speicherort einer VHD auf -- entweder eine Cluster Shared
    Volume (csv_name gesetzt) ODER ein NetApp-CIFS-Export (smb_server/
    smb_share gesetzt), nie beides (siehe HyperVVhd). CSV wird zuerst
    versucht (der bisherige, weit verbreitetere Fall); nur wenn das
    fehlschlaegt, wird auf einen UNC-Pfad geprueft (Backlog #22)."""
    csv_name = _resolve_csv_name(_parse_csv_name(vhd_path), csvs)
    if csv_name:
        return csv_name, None, None
    smb = _parse_smb_share(vhd_path)
    if smb:
        return None, smb[0], smb[1]
    return None, None, None


def _refresh_smb_share_rows(db: Session, cluster_id: str, vhds: list[HyperVVhd]) -> None:
    """Leitet HyperVSmbShare-Zeilen rein aus den bereits discoverten VHD-
    Zeilen ab (Gruppierung nach Server+Freigabe) -- anders als
    _refresh_csv_rows gibt es keine eigene WinRM-Abfrage dafuer, ein
    SMB3-Share ist kein Windows-Cluster-Ressourcenobjekt (siehe
    HyperVSmbShare-Docstring). Die Zuordnung zum NetApp-Volume erfolgt
    ueber einen direkten Server+Freigabename-Abgleich, kein Seriennummer-
    Umweg wie bei CSV/LUN."""
    now = datetime.now(timezone.utc)
    netapp_cluster_names = {c.id: c.ontap_cluster_name or c.name for c in db.query(NetAppCluster).all()}
    cifs_server_by_svm: dict[tuple[str, str], str] = {
        (svm.cluster_id, svm.name): svm.cifs_server_name for svm in db.query(NetAppSvm).all() if svm.cifs_server_name
    }
    shares_by_server_and_name: dict[str, NetAppCifsShare] = {}
    for share in db.query(NetAppCifsShare).all():
        server = cifs_server_by_svm.get((share.cluster_id, share.svm_name or ""))
        if server:
            shares_by_server_and_name[f"{server.lower()}::{share.name.lower()}"] = share

    volumes_by_key = {(v.cluster_id, v.svm_name, v.name): v for v in db.query(NetAppVolume).all()}

    groups: set[tuple[str, str]] = set()
    for vhd in vhds:
        if vhd.smb_server and vhd.smb_share:
            groups.add((vhd.smb_server, vhd.smb_share))

    db.query(HyperVSmbShare).filter(HyperVSmbShare.cluster_id == cluster_id).delete()
    for server, share_name in groups:
        share = shares_by_server_and_name.get(f"{server.lower()}::{share_name.lower()}")
        # Kapazitaet kommt (wie bei HyperVCsv) vom zugrunde liegenden
        # NetApp-Objekt -- fuer SMB3 gibt es kein Windows-Cluster-
        # Ressourcenobjekt, das eine eigene Groesse melden koennte, daher
        # direkt vom korrelierten NetAppVolume uebernommen.
        volume = volumes_by_key.get((share.cluster_id, share.svm_name, share.volume_name)) if share else None
        db.add(
            HyperVSmbShare(
                cluster_id=cluster_id, server=server, share=share_name,
                capacity_bytes=volume.size_bytes if volume else None,
                used_bytes=volume.used_bytes if volume else None,
                netapp_cifs_share_id=share.id if share else None,
                netapp_volume_name=share.volume_name if share else None,
                netapp_svm_name=share.svm_name if share else None,
                netapp_cluster_name=netapp_cluster_names.get(share.cluster_id) if share else None,
                last_seen_at=now,
            )
        )
    db.commit()


def _refresh_csv_rows(db: Session, cluster_id: str, csvs: list[ClusterSharedVolumeInfo]) -> None:
    """Ersetzt alle HyperVCsv-Zeilen eines Clusters aus einem frischen
    list_csvs()-Ergebnis -- ausgelagert aus der vollstaendigen Discovery,
    damit auch ein einzelner Restore (ADD/REPLACE, siehe app.api.routes.
    restore) danach die CSV-Auslastung aktualisieren kann, ohne eine
    komplette Cluster-Discovery (inkl. VM-Neuabfrage aller Knoten)
    anzustossen. Loescht/ersetzt ALLE CSVs des Clusters statt nur einer
    einzelnen Zeile -- list_csvs() liest ohnehin den kompletten
    Cluster-Datenbestand in einem Single-Hop-Aufruf gegen den CNO, ein
    gezielteres Teil-Update waere nicht guenstiger."""
    now = datetime.now(timezone.utc)
    # Seriennummer -> NetApp-LUN ueber alle registrierten NetApp-Cluster
    # hinweg (die Windows-Disk-Seriennummer entspricht ONTAP's
    # lun.serial_number, siehe list_csvs()); Clustername separat
    # aufloesen, da NetAppLun selbst nur die cluster_id speichert.
    netapp_cluster_names = {c.id: c.ontap_cluster_name or c.name for c in db.query(NetAppCluster).all()}
    luns_by_serial = {
        lun.serial_number: lun for lun in db.query(NetAppLun).all() if lun.serial_number
    }
    db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).delete()
    for csv in csvs:
        lun = luns_by_serial.get(csv.disk_serial_number) if csv.disk_serial_number else None
        db.add(
            HyperVCsv(
                cluster_id=cluster_id, name=csv.name, path=csv.volume_path, owner_node=csv.owner_node,
                state=csv.state, capacity_bytes=csv.capacity_bytes, used_bytes=csv.used_bytes,
                disk_serial_number=csv.disk_serial_number,
                netapp_lun_id=lun.id if lun else None,
                netapp_lun_name=lun.name if lun else None,
                netapp_volume_name=lun.volume_name if lun else None,
                netapp_svm_name=lun.svm_name if lun else None,
                netapp_cluster_name=netapp_cluster_names.get(lun.cluster_id) if lun else None,
                last_seen_at=now,
            )
        )
    db.commit()


def _apply_vm_discovery_refresh(db: Session, cluster_id: str, vm: HyperVVm, refreshed: VirtualMachineInfo) -> None:
    """Ersetzt Checkpoint-Liste und VHD-Zeilen EINER VM aus einem frischen
    get_vm()-Ergebnis -- gemeinsame Logik fuer die manuelle Checkpoint-
    Loeschung UND die manuelle "VM Discovery"-Aktion (beide
    app.api.routes.vms) sowie den Auto-Refresh nach dem automatischen
    Entfernen eines Backup-Checkpoints (_execute_job_run, app.api.routes.
    jobs). `vm` muss bereits die aktuelle DB-Zeile sein (frisch
    nachgeladen, NIE ueber den vorausgehenden WinRM-Aufruf hinweg gehalten
    -- siehe [[backup-vs-discovery-orm-race]])."""
    vm.checkpoints = [
        {"name": c.name, "id": c.id, "creation_time": c.creation_time, "hard_drive_paths": c.hard_drive_paths}
        for c in refreshed.checkpoints
    ]
    # HyperVSmbShare selbst wird hier bewusst NICHT aktualisiert (anders als
    # HyperVCsv-Zeilen bereits vorher, siehe existing_csvs) -- das ist ein
    # Einzel-VM-Refresh, _refresh_smb_share_rows braucht aber den VHD-Stand
    # DES GESAMTEN Clusters (sonst wuerden die Shares aller anderen VMs
    # faelschlich verworfen). Bleibt der vollen Discovery (_run_discovery)
    # vorbehalten, exakt analog zu _refresh_csv_rows, das hier ebenfalls
    # nicht aufgerufen wird.
    existing_csvs = db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).all()
    db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id, HyperVVhd.vm_uuid == vm.vm_uuid).delete()
    now = datetime.now(timezone.utc)
    for vhd in refreshed.vhds:
        csv_name, smb_server, smb_share = _resolve_vhd_location(vhd.path, existing_csvs)
        db.add(
            HyperVVhd(
                cluster_id=cluster_id, vm_uuid=vm.vm_uuid, vm_name=vm.name, path=vhd.path,
                csv_name=csv_name, smb_server=smb_server, smb_share=smb_share,
                size_bytes=vhd.size_bytes, used_bytes=vhd.used_bytes,
                base_size_bytes=vhd.base_size_bytes, base_used_bytes=vhd.base_used_bytes, last_seen_at=now,
            )
        )


def _get_vm_settled(
    node_service: HyperVService,
    node_session,
    vm_name: str,
    attempts: int = 4,
    delay_sec: float = 5.0,
    username: str | None = None,
    password: str | None = None,
) -> VirtualMachineInfo | None:
    """get_vm() mit kurzem, begrenztem Retry -- fuer eine LAUFENDE VM laeuft
    der AVHDX->VHDX-Merge nach Remove-VMSnapshot asynchron im Hintergrund
    weiter (bei einer ausgeschalteten VM ist er dagegen synchron). Eine
    Abfrage direkt nach dem Entfernen kann daher ehrlich noch die AVHDX
    zeigen, obwohl der Merge Sekunden bis wenige Minuten spaeter laengst
    fertig ist -- live beobachtet 2026-09-11 (RestoreTestVM_PG2): die erste
    Abfrage direkt nach dem Loeschen zeigte noch AVHDX, derselbe Aufruf 4
    Minuten spaeter bereits die normale VHDX, ohne dass sich sonst etwas
    geaendert hatte. Bricht sofort ab, sobald keine .avhdx mehr uebrig ist
    (der haeufige, schnelle Fall) oder wieder ein Checkpoint auftaucht
    (z.B. ein zwischenzeitlich neu erstellter). Nur fuer manuelle,
    synchrone Einzel-VM-Aktionen (Checkpoint-Loeschung, "VM Discovery") --
    NICHT im automatisierten Backup-Pfad verwendet, dort soll ein
    haengender/langsamer Merge nicht die Laufzeit des gesamten Laufs
    verlaengern (das Sicherheitsnetz dort ist der Alarm hyperv_vm_avhdx_
    without_checkpoint, siehe scheduler.py).

    'username'/'password' (optional): siehe HyperVService.list_vms --
    noetig, damit Get-VHD fuer eine SMB3-hostete VM (#22) nicht am
    Double-Hop-Problem scheitert und dabei still 0 Bytes liefert."""
    result = node_service.get_vm(node_session, vm_name, username=username, password=password)
    for _ in range(attempts - 1):
        if result is None or result.checkpoints:
            break
        if not any(v.path.lower().endswith(".avhdx") for v in result.vhds):
            break
        time.sleep(delay_sec)
        result = node_service.get_vm(node_session, vm_name, username=username, password=password)
    return result


def _get_cluster_or_404(db: Session, cluster_id: str) -> HyperVCluster:
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster nicht gefunden")
    return cluster


def _service_for(cluster: HyperVCluster) -> HyperVService:
    # node_hostname=cluster.hyperv_cluster_name: fuer Kerberos benoetigt
    # (SPN-Aufloesung ist hostnamenbasiert, cluster.management_address ist
    # i.d.R. eine IP) -- fuer NTLM/CredSSP wirkungslos. Wird von _refresh_status
    # (manueller Verify-Button UND periodische Discovery, siehe scheduler.py)
    # sowie der eigentlichen VM/CSV-Discovery genutzt.
    return HyperVService(
        get_settings(), cluster.management_address, use_https=cluster.use_https, node_hostname=cluster.hyperv_cluster_name,
    )


def _apply_summary(cluster: HyperVCluster, summary) -> None:
    cluster.hyperv_cluster_name = summary.cluster_name
    cluster.node_count = summary.node_count
    cluster.healthy_node_count = summary.healthy_node_count
    cluster.health = (
        HyperVClusterHealth.HEALTHY
        if summary.node_count > 0 and summary.healthy_node_count == summary.node_count
        else HyperVClusterHealth.DEGRADED
    )
    cluster.last_check_error = None


def _refresh_node_reachability(cluster: HyperVCluster, service: HyperVService, username: str, password: str) -> None:
    """Ergaenzt cluster.unreachable_nodes_json um das Ergebnis eines
    direkten Erreichbarkeits-Checks pro Cluster-Knoten (siehe
    HyperVService.check_node_reachability) -- rein additiv zur eigentlichen
    Cluster-Health oben: ein Fehler hier (z.B. Get-ClusterNode selbst
    schlaegt fehl) laesst den Health-Check/die Cluster-Anlage NICHT
    scheitern, sondern belaesst nur den zuletzt bekannten Node-Status."""
    try:
        cno_session = service.connect(username, password, read_timeout_sec=15, operation_timeout_sec=10)
        node_results = service.check_node_reachability(cno_session, username, password)
        unreachable = [
            {"name": r.name, "address": r.address, "error": r.error} for r in node_results if not r.reachable
        ]
        cluster.unreachable_nodes_json = json.dumps(unreachable) if unreachable else None
    except Exception:
        pass


def _refresh_status(db: Session, cluster: HyperVCluster) -> HyperVCluster:
    service = _service_for(cluster)
    try:
        password = decrypt_secret(cluster.encrypted_password)
        summary = service.get_cluster_summary(cluster.username, password)
        _apply_summary(cluster, summary)
        _refresh_node_reachability(cluster, service, cluster.username, password)
    except HyperVConnectionError as exc:
        cluster.health = HyperVClusterHealth.UNREACHABLE
        cluster.last_check_error = str(exc)
    cluster.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.get("", response_model=list[HyperVClusterRead])
def list_clusters(db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_VIEW))) -> list[HyperVCluster]:
    return db.query(HyperVCluster).order_by(HyperVCluster.name).all()


@router.post("/check-reachability")
def check_reachability_route(
    payload: HyperVReachabilityCheck, user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> dict:
    port = 5986 if payload.use_https else 5985
    try:
        check_reachability(payload.management_address, port)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "reachable"}


@router.post("", response_model=HyperVClusterRead, status_code=status.HTTP_201_CREATED)
def create_cluster(
    payload: HyperVClusterCreate,
    db: Session = Depends(get_db),
    # HYPERV_CLUSTER_MANAGE statt HYPERV_MANAGE, da nur Administrator einen
    # ganzen Cluster an-/abbauen darf (Operator verwaltet nur VMs/
    # Checkpoints/Discovery innerhalb bereits registrierter Cluster,
    # Nutzer-Vorgabe 2026-09-17).
    user=Depends(require_permission(Permission.HYPERV_CLUSTER_MANAGE)),
) -> HyperVCluster:
    if db.query(HyperVCluster).filter(HyperVCluster.name == payload.name).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Cluster mit diesem Namen existiert bereits")

    probe = HyperVService(get_settings(), payload.management_address, use_https=payload.use_https)
    try:
        summary = probe.get_cluster_summary(payload.username, payload.password)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Verbindung fehlgeschlagen: {exc}") from exc

    cluster = HyperVCluster(
        name=payload.name,
        management_address=payload.management_address,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        use_https=payload.use_https,
        last_checked_at=datetime.now(timezone.utc),
    )
    _apply_summary(cluster, summary)
    _refresh_node_reachability(cluster, probe, payload.username, payload.password)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.put("/{cluster_id}", response_model=HyperVClusterRead)
def update_cluster(
    cluster_id: str,
    payload: HyperVClusterUpdate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> HyperVCluster:
    """Aendert die Verbindungsdaten eines bereits registrierten Clusters IN
    PLACE (gleiche cluster.id bleibt erhalten) -- insbesondere fuer eine
    Passwort-Rotation gedacht. Vorher gab es dafuer nur Loeschen+Neuanlegen,
    was aber eine NEUE cluster.id erzeugt haette: ResourceGroup.members
    referenziert Cluster-Mitglieder Cluster-qualifiziert (`<cluster_id>::
    <name>`, siehe make_member_key), ein Neuanlegen haette also alle
    bestehenden Protection-Group-Zuordnungen zu diesem Cluster stillschweigend
    kaputt gemacht -- nicht nur die discoverten Inventardaten."""
    cluster = _get_cluster_or_404(db, cluster_id)
    name_conflict = (
        db.query(HyperVCluster).filter(HyperVCluster.name == payload.name, HyperVCluster.id != cluster_id).first()
    )
    if name_conflict is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ein Cluster mit diesem Namen existiert bereits")

    effective_password = payload.password or decrypt_secret(cluster.encrypted_password)
    probe = HyperVService(get_settings(), payload.management_address, use_https=payload.use_https)
    try:
        summary = probe.get_cluster_summary(payload.username, effective_password)
    except HyperVConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Verbindung fehlgeschlagen: {exc}") from exc

    cluster.name = payload.name
    cluster.management_address = payload.management_address
    cluster.username = payload.username
    if payload.password:
        cluster.encrypted_password = encrypt_secret(payload.password)
    cluster.use_https = payload.use_https
    cluster.last_checked_at = datetime.now(timezone.utc)
    _apply_summary(cluster, summary)
    _refresh_node_reachability(cluster, probe, payload.username, effective_password)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.post("/{cluster_id}/verify", response_model=HyperVClusterRead)
def verify_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
) -> HyperVCluster:
    cluster = _get_cluster_or_404(db, cluster_id)
    return _refresh_status(db, cluster)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_CLUSTER_MANAGE)),
) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)
    # HyperVVm/-Vhd/-Csv sind zwar mit ForeignKey(..., ondelete="CASCADE")
    # deklariert, aber SQLite erzwingt das nur, wenn PRAGMA foreign_keys=ON
    # pro Verbindung gesetzt wird -- das passiert in dieser App nirgends,
    # die CASCADE-Angabe im Modell ist also reine Dokumentation ohne
    # Wirkung. Ohne diesen expliziten Cleanup blieben discoverte VMs/CSVs
    # des geloeschten Clusters als Stale-Entries stehen (live beobachtet:
    # fuehrte bei erneutem Hinzufuegen desselben Clusters zu doppelten
    # VM-Eintraegen im Inventory, siehe Backlog).
    db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster_id).delete()
    db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster_id).delete()
    db.query(HyperVCsv).filter(HyperVCsv.cluster_id == cluster_id).delete()
    db.query(HyperVSmbShare).filter(HyperVSmbShare.cluster_id == cluster_id).delete()
    db.delete(cluster)
    db.commit()


def _run_discovery(db: Session, cluster: HyperVCluster) -> list:
    """Kernlogik von discover_cluster() -- ausgelagert, damit der periodische
    Discovery-Job (app.core.scheduler) und der manuelle 'Discover'-Button in
    der GUI exakt denselben Code nutzen, statt ihn zu duplizieren."""
    service = _service_for(cluster)
    steps, data = service.run_discovery(cluster.username, decrypt_secret(cluster.encrypted_password))

    # Nur ersetzen, wenn mindestens ein Knoten erfolgreich abgefragt wurde --
    # sonst wuerde ein voruebergehend nicht erreichbarer Cluster die bereits
    # bekannten VMs faelschlich loeschen (analog zur NetApp-Discovery).
    if any(s.success for s in steps if s.step == "vms"):
        now = datetime.now(timezone.utc)
        db.query(HyperVVhd).filter(HyperVVhd.cluster_id == cluster.id).delete()
        db.query(HyperVVm).filter(HyperVVm.cluster_id == cluster.id).delete()
        # data.csvs stammt aus demselben Discovery-Lauf -- ermoeglicht, den
        # von _parse_csv_name() aus dem VHD-Pfad ermittelten Mount-
        # Ordnernamen sofort auf den tatsaechlichen (ggf. umbenannten)
        # CSV-Namen aufzuloesen (siehe _resolve_csv_name).
        #
        # Dedupe nach der stabilen Hyper-V-VM-GUID (vm.id), letztes
        # Vorkommen gewinnt: laeuft die Discovery waehrend einer aktiven
        # Live-Migration, kann dieselbe VM kurzzeitig auf ZWEI Knoten
        # gleichzeitig als lokal gemeldet werden (jeder Knoten wird per
        # list_vms() einzeln abgefragt, siehe HyperVService.run_discovery)
        # -- ohne Dedup entstehen zwei HyperVVm-Zeilen mit identischem
        # (cluster_id, name). Live gefunden: landet ein solcher Duplikat-
        # Name ungefiltert in einer Mantine-Select-Optionsliste (z.B. der
        # Objektauswahl beim Anlegen einer Protection Group), wirft Mantine
        # einen harten, von keinem Error Boundary abgefangenen Rendering-
        # Fehler -- das gesamte Fenster wurde weiss.
        deduped_vms = list({vm.id: vm for vm in data.vms}.values())
        all_vhds: list[HyperVVhd] = []
        for vm in deduped_vms:
            db.add(
                HyperVVm(
                    cluster_id=cluster.id, vm_uuid=vm.id, name=vm.name, state=vm.state, host_name=vm.host, last_seen_at=now,
                    cpu_count=vm.cpu_count, generation=vm.generation,
                    memory_startup_bytes=vm.memory_startup_bytes, memory_minimum_bytes=vm.memory_minimum_bytes,
                    memory_maximum_bytes=vm.memory_maximum_bytes, dynamic_memory_enabled=vm.dynamic_memory_enabled,
                    network_adapters=[
                        {"name": n.name, "mac_address": n.mac_address, "switch_name": n.switch_name, "vlan_id": n.vlan_id}
                        for n in vm.network_adapters
                    ],
                    pci_devices=vm.pci_devices,
                    checkpoints=[{"name": c.name, "id": c.id, "creation_time": c.creation_time} for c in vm.checkpoints],
                )
            )
            for vhd in vm.vhds:
                csv_name, smb_server, smb_share = _resolve_vhd_location(vhd.path, data.csvs)
                vhd_row = HyperVVhd(
                    cluster_id=cluster.id, vm_uuid=vm.id, vm_name=vm.name, path=vhd.path,
                    csv_name=csv_name, smb_server=smb_server, smb_share=smb_share,
                    size_bytes=vhd.size_bytes, used_bytes=vhd.used_bytes,
                    base_size_bytes=vhd.base_size_bytes, base_used_bytes=vhd.base_used_bytes,
                    last_seen_at=now,
                )
                db.add(vhd_row)
                all_vhds.append(vhd_row)
        db.commit()

        # SMB3-Freigaben (Backlog #22) rein aus dem VHD-Stand dieses
        # Discovery-Laufs abgeleitet -- braucht mindestens einen
        # erfolgreichen "vms"-Schritt (siehe all_vhds oben), unabhaengig
        # vom CSV-Schritt-Erfolg (ein Cluster kann ausschliesslich SMB3-
        # gehostete VMs haben, ganz ohne CSV).
        _refresh_smb_share_rows(db, cluster.id, all_vhds)

    if any(s.success for s in steps if s.step == "csvs"):
        _refresh_csv_rows(db, cluster.id, data.csvs)

    return steps


@router.post("/{cluster_id}/discover", response_model=list[DiscoveryStepRead])
def discover_cluster(
    cluster_id: str, db: Session = Depends(get_db), user=Depends(require_permission(Permission.HYPERV_MANAGE)),
):
    cluster = _get_cluster_or_404(db, cluster_id)
    # Discovery und ein laufender Backup-Lauf duerfen sich nicht
    # ueberschneiden: die volle Discovery loescht/legt HyperVVm-Zeilen neu
    # an (der Backup-Lauf haelt sie -- ObjectDeletedError, siehe
    # _execute_job_run) UND fuehrt Get-VHD ueber die (waehrend eines
    # Backup-Checkpoint-Merges gesperrte) AVHDX-Kette aus (Hang). Die
    # periodische Discovery verschiebt sich in diesem Fall selbst (siehe
    # run_discovery/_DISCOVERY_MAX_DEFERRALS in app.core.scheduler) -- der
    # manuelle Button hatte diesen Schutz bisher nicht.
    if db.query(BackupRun.id).filter(BackupRun.status == JobStatus.RUNNING).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ein Backup-Lauf ist gerade aktiv -- Discovery wuerde damit kollidieren. Bitte nach dessen Abschluss erneut versuchen.",
        )
    return _run_discovery(db, cluster)
