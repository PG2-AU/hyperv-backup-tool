"""Neue SMB3-Freigabe fuer Hyper-V anlegen (Nutzer-Vorgaben 2026-09-30),
Gegenstueck zu app.api.routes.csv_create: Button "Neue Freigabe" in
Inventory > SMB3-Freigaben. Hintergrund-Task mit Schritt-Protokoll:

  Vorpruefung -> Volume anlegen (eingehaengt, NTFS) -> CIFS-Freigabe
  anlegen (continuously available, Freigaberechte) -> Zugriff von jedem
  Knoten pruefen -> Inventory -> Protection Group (optional)

Standardwerte: Volume wie bei der CSV (thin, Snapshot-Policy none, Reserve
0 %, Autosize grow optional) plus Junction /<volume> und Sicherheitsstil
NTFS; Freigabename = Volume-Name (aenderbar). Freigaberechte Vollzugriff fuer
die Computerkonten aller Knoten, des Clusters (CNO) und des Restore-Proxy-
Hosts sowie BUILTIN\\Administrators -- OHNE 'Everyone' (Nutzer-Vorgabe; den
setzt ONTAP von sich aus, er wird danach entfernt). Die NTFS-Rechte der
Volume-Wurzel bleiben ONTAP-Standard; Hyper-V ergaenzt beim ersten Zugriff,
was es braucht.

Hyper-V braucht keine eigene Aktion (kein Cluster-Objekt fuer SMB3); der
Zugriffstest laeuft per CredSSP (Double-Hop zur Freigabe) und gilt nur als
Warnung. Bei einem Fehler davor: Rueckfrage Zurueckrollen/Behalten wie bei
der CSV."""

import copy
import ipaddress
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_user_permissions
from app.api.routes.csv_create import _Cluster, _hyperv_service, _soft_step
from app.api.routes.csv_resize import _require_hyperv_manage
from app.api.routes.hyperv_clusters import _refresh_smb_share_rows
from app.api.routes.jobs import _smb_share_key
from app.api.routes.netapp_clusters import _discover_and_persist
from app.api.routes.netapp_clusters import _service_for as _netapp_service_for
from app.api.routes.restore import _StepCtx
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.sites import _is_mcc_mirror_svm
from app.db.session import SessionLocal, get_db
from app.models.backup_policy import BackupScope
from app.models.hyperv_cluster import HyperVCluster
from app.models.hyperv_discovery import HyperVSmbShare, HyperVVhd
from app.models.netapp_cluster import NetAppCluster
from app.models.netapp_discovery import NetAppSvm
from app.models.resource_group import ResourceGroup, make_member_key
from app.models.restore_proxy_host import RestoreProxyHost
from app.models.restore_run import RestoreStatus
from app.models.smb_share_run import SmbCreateRun, SmbCreateRunStep
from app.models.system_log import SystemLogEvent

router = APIRouter(prefix="/api/smb-create", tags=["smb-create"])

GIB = 1024**3
_VOLUME_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,202}$")
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$")
_ACCOUNT_RE = re.compile(r"^[^\\/:*?\"<>|]+\\[^\\/:*?\"<>|]+$")
ADMINS = "BUILTIN\\Administrators"


class AccountSuggestion(BaseModel):
    account: str
    source: str


class SmbHyperVOptions(BaseModel):
    cluster_id: str
    domain: str
    accounts: list[AccountSuggestion]
    existing_shares: list[str]
    busy_reason: str | None = None
    warnings: list[str] = []


class SmbSvmOption(BaseModel):
    name: str
    cifs_server: str


class SmbNetAppOptions(BaseModel):
    netapp_cluster_id: str
    svms: list[SmbSvmOption]
    aggregates: list[dict]


class SmbCreateRequest(BaseModel):
    cluster_id: str
    netapp_cluster_id: str
    svm_name: str = Field(min_length=1)
    aggregate_name: str | None = None
    volume_name: str
    share_name: str
    volume_size_bytes: int = Field(ge=GIB)
    autosize_grow: bool = True
    accounts: list[str] = Field(min_length=1)
    resource_group_id: str | None = None


class StepRead(BaseModel):
    step: str
    label: str
    status: str
    message: str | None = None

    class Config:
        from_attributes = True


class SmbCreateRunRead(BaseModel):
    id: str
    svm_name: str
    volume_name: str
    share_name: str
    volume_size_bytes: int
    cifs_server: str | None = None
    unc_path: str | None = None
    created_volume_uuid: str | None = None
    share_created: bool = False
    rollback_declined: bool = False
    has_created_objects: bool = False
    status: str
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[StepRead]

    class Config:
        from_attributes = True


def _gb(value: int | None) -> str:
    return f"{(value or 0) / GIB:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _log(db: Session, message: str, level: str = "INFO") -> None:
    db.add(SystemLogEvent(level=level, source="storage", message=message))
    db.commit()


def _busy_reason(db: Session, cluster_id: str, own_run_id: str | None = None) -> str | None:
    running = (
        db.query(SmbCreateRun)
        .filter(
            SmbCreateRun.hyperv_cluster_id == cluster_id, SmbCreateRun.status == RestoreStatus.RUNNING,
            SmbCreateRun.id != (own_run_id or ""),
        )
        .first()
    )
    if running:
        return f"Auf diesem Cluster wird gerade die Freigabe '{running.share_name}' angelegt bzw. zurückgerollt -- bitte das Ende abwarten."
    return None


def _proxy_account_name(db: Session) -> str | None:
    """Computername des Restore-Proxy-Hosts (fuer DOMAIN\\NAME$) -- aus dem
    hinterlegten Hostnamen, sonst aus der Adresse, sofern das keine IP ist."""
    proxy = db.query(RestoreProxyHost).first()
    if proxy is None:
        return None
    for candidate in (proxy.hostname, proxy.address):
        value = (candidate or "").strip()
        if not value:
            continue
        try:
            ipaddress.ip_address(value)
            continue
        except ValueError:
            return value.split(".")[0].upper()
    return None


def _computer_account(domain: str, name: str) -> str:
    return f"{domain}\\{name.split('.')[0].upper()}$"


# --- Auswahl-Optionen ------------------------------------------------------------------


@router.get("/hyperv/{cluster_id}", response_model=SmbHyperVOptions)
def hyperv_options(cluster_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> SmbHyperVOptions:
    """Vorschlag fuer die Freigaberechte: Knoten, CNO, Restore-Proxy-Host,
    Administratoren."""
    cluster = db.get(HyperVCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster nicht gefunden")
    try:
        cno = _Cluster(cluster, decrypt_secret(cluster.encrypted_password))
        info = cno.service.cluster_computer_accounts(cno.session)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Hyper-V-Abfrage fehlgeschlagen: {exc}") from exc
    domain = info["domain"]
    warnings = []
    accounts = [AccountSuggestion(account=_computer_account(domain, n), source=f"Knoten {n}") for n in sorted(info["nodes"], key=str.lower)]
    if info["cluster"]:
        accounts.append(AccountSuggestion(account=_computer_account(domain, info["cluster"]), source=f"Cluster (CNO) {info['cluster']}"))
    proxy = _proxy_account_name(db)
    if proxy:
        accounts.append(AccountSuggestion(account=_computer_account(domain, proxy), source=f"Restore-Proxy-Host {proxy}"))
    else:
        warnings.append(
            "Restore-Proxy-Host: kein Computername bekannt (nicht eingerichtet oder nur als IP hinterlegt) -- "
            "Konto bei Bedarf von Hand ergänzen."
        )
    accounts.append(AccountSuggestion(account=ADMINS, source="lokale Administratoren"))
    existing = sorted({f"{r.server}\\{r.share}" for r in db.query(HyperVSmbShare).filter(HyperVSmbShare.cluster_id == cluster_id)})
    return SmbHyperVOptions(
        cluster_id=cluster_id, domain=domain, accounts=accounts, existing_shares=existing,
        busy_reason=_busy_reason(db, cluster_id), warnings=warnings,
    )


@router.get("/netapp/{netapp_cluster_id}", response_model=SmbNetAppOptions)
def netapp_options(netapp_cluster_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> SmbNetAppOptions:
    netapp = db.get(NetAppCluster, netapp_cluster_id)
    if netapp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetApp-System nicht gefunden")
    svms = [
        SmbSvmOption(name=s.name, cifs_server=s.cifs_server_name)
        for s in db.query(NetAppSvm).filter(NetAppSvm.cluster_id == netapp_cluster_id).order_by(NetAppSvm.name).all()
        if s.cifs_server_name and not _is_mcc_mirror_svm(s) and (s.state or "running").lower() == "running"
    ]
    try:
        aggregates = _netapp_service_for(netapp).csv_create_aggregates()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NetApp-Abfrage fehlgeschlagen: {exc}") from exc
    return SmbNetAppOptions(netapp_cluster_id=netapp_cluster_id, svms=svms, aggregates=[a for a in aggregates if a.get("name")])


# --- Start / Rueckfrage ----------------------------------------------------------------


def _validate(db: Session, payload: SmbCreateRequest, user) -> None:
    errors = []
    if not _VOLUME_NAME_RE.match(payload.volume_name):
        errors.append("Volume-Name: nur Buchstaben, Ziffern und '_' (erstes Zeichen Buchstabe oder '_').")
    if not _SHARE_NAME_RE.match(payload.share_name):
        errors.append("Freigabename: nur Buchstaben, Ziffern, '_', '-', '.' (max. 80 Zeichen).")
    bad = [a for a in payload.accounts if not _ACCOUNT_RE.match(a.strip())]
    if bad:
        errors.append(f"Konten bitte als DOMÄNE\\Name angeben: {', '.join(bad)}.")
    if any(a.strip().lower() == "everyone" for a in payload.accounts):
        errors.append("'Everyone' wird für Hyper-V-Freigaben bewusst nicht vergeben.")
    if payload.resource_group_id:
        group = db.get(ResourceGroup, payload.resource_group_id)
        if group is None or group.scope != BackupScope.SMB_SHARE:
            errors.append("Die gewählte Protection Group gibt es nicht oder sie enthält keine SMB3-Freigaben.")
        elif Permission.BACKUP_CREATE not in get_user_permissions(user, db):
            errors.append("Für die Zuordnung zu einer Protection Group fehlt die Berechtigung backup:create.")
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))


@router.get("/runs/{run_id}", response_model=SmbCreateRunRead)
def get_run(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> SmbCreateRun:
    run = db.get(SmbCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    return run


@router.post("", response_model=SmbCreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_create(
    payload: SmbCreateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
    user=Depends(_require_hyperv_manage),
) -> SmbCreateRun:
    if db.get(HyperVCluster, payload.cluster_id) is None or db.get(NetAppCluster, payload.netapp_cluster_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hyper-V-Cluster oder NetApp-System nicht gefunden")
    _validate(db, payload, user)
    busy = _busy_reason(db, payload.cluster_id)
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    accounts = list(dict.fromkeys(a.strip() for a in payload.accounts if a.strip()))
    run = SmbCreateRun(
        hyperv_cluster_id=payload.cluster_id, netapp_cluster_id=payload.netapp_cluster_id, svm_name=payload.svm_name,
        volume_name=payload.volume_name, share_name=payload.share_name, volume_size_bytes=payload.volume_size_bytes,
        options={
            "aggregate_name": payload.aggregate_name, "autosize_grow": payload.autosize_grow, "accounts": accounts,
            "resource_group_id": payload.resource_group_id,
        },
        requested_by=user.display_name or user.username, status=RestoreStatus.RUNNING, started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_execute_create, run.id)
    return run


@router.post("/runs/{run_id}/rollback", response_model=SmbCreateRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_rollback(
    run_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage),
) -> SmbCreateRun:
    run = db.get(SmbCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status != RestoreStatus.FAILED or not run.has_created_objects or run.rollback_declined:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Für diesen Lauf gibt es nichts zurückzurollen.")
    run.status = RestoreStatus.RUNNING
    run.finished_at = None
    db.commit()
    _log(db, f"SMB3-Freigabe '{run.share_name}' anlegen: Zurückrollen gestartet (durch {user.display_name or user.username})")
    background_tasks.add_task(_execute_rollback, run.id)
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/keep", response_model=SmbCreateRunRead)
def keep_objects(run_id: str, db: Session = Depends(get_db), user=Depends(_require_hyperv_manage)) -> SmbCreateRun:
    run = db.get(SmbCreateRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lauf nicht gefunden")
    if run.status == RestoreStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Lauf ist noch aktiv.")
    run.rollback_declined = True
    db.commit()
    _log(db, f"SMB3-Freigabe '{run.share_name}' anlegen: angelegte Objekte nach Fehler bewusst behalten (durch {user.display_name or user.username})")
    return run


# --- Ausfuehrung ----------------------------------------------------------------------


def _execute_create(run_id: str) -> None:  # noqa: C901
    db = SessionLocal()
    try:
        run = db.get(SmbCreateRun, run_id)
        if run is None:
            return
        opts = run.options or {}
        accounts: list[str] = opts.get("accounts", [])
        try:
            with _StepCtx(db, run.id, "precheck", "Vorprüfung", step_model=SmbCreateRunStep) as ctx:
                cluster = db.get(HyperVCluster, run.hyperv_cluster_id)
                netapp_cluster = db.get(NetAppCluster, run.netapp_cluster_id)
                if cluster is None or netapp_cluster is None:
                    raise RuntimeError("Hyper-V-Cluster oder NetApp-System nicht mehr vorhanden")
                netapp = _netapp_service_for(netapp_cluster)
                run.cifs_server = netapp.svm_cifs_server(run.svm_name)
                if not run.cifs_server:
                    raise RuntimeError(f"SVM '{run.svm_name}' hat keinen CIFS-Server")
                if netapp.volume_exists(run.svm_name, run.volume_name):
                    raise RuntimeError(f"Volume '{run.volume_name}' gibt es auf SVM '{run.svm_name}' bereits")
                if netapp.cifs_share_exists(run.svm_name, run.share_name):
                    raise RuntimeError(f"Freigabe '{run.share_name}' gibt es auf SVM '{run.svm_name}' bereits")
                ctx.row.message = f"\\\\{run.cifs_server}\\{run.share_name}, {len(accounts)} Konten"

            with _StepCtx(db, run.id, "volume", f"Volume {run.volume_name} anlegen", step_model=SmbCreateRunStep) as ctx:
                run.created_volume_uuid = netapp.create_csv_volume(
                    run.svm_name, run.volume_name, opts.get("aggregate_name"), run.volume_size_bytes,
                    autosize_grow=bool(opts.get("autosize_grow")), junction_path=f"/{run.volume_name}",
                )
                ctx.row.message = (
                    f"{_gb(run.volume_size_bytes)} auf {opts.get('aggregate_name') or 'von ONTAP gewähltem Aggregat'}, "
                    f"Junction /{run.volume_name}, NTFS, thin, Snapshot-Policy none, Reserve 0 %"
                    + (", Autosize grow" if opts.get("autosize_grow") else "")
                )

            with _StepCtx(db, run.id, "share", f"Freigabe {run.share_name} anlegen", step_model=SmbCreateRunStep) as ctx:
                netapp.create_cifs_share(
                    run.svm_name, run.volume_name, run.share_name, path=f"/{run.volume_name}",
                    acls=[{"user_or_group": a, "permission": "full_control", "type": "windows"} for a in accounts],
                    properties={"continuously_available": True, "oplocks": True},
                )
                run.share_created = True
                db.commit()
                removed = netapp.remove_share_acl(run.svm_name, run.share_name, "Everyone")
                acl = netapp.share_acl_names(run.svm_name, run.share_name)
                ctx.row.message = (
                    f"continuously available; Vollzugriff: {', '.join(acl)}" + (" ('Everyone' entfernt)" if removed else "")
                )

            warnings: list[str] = []
            unc = run.unc_path

            def _access() -> str:
                cno = _Cluster(cluster, decrypt_secret(cluster.encrypted_password))
                node_ips = cno.service.node_address_map(cno.session)
                settings = copy.copy(cno.settings)
                settings.winrm_transport = "credssp"
                ok, failed = [], []
                for node in cno.service.list_cluster_nodes(cno.session):
                    if node.state not in ("Up", "Paused"):
                        continue
                    try:
                        service = _hyperv_service(cluster, node_ips.get(node.name.lower(), node.name), node.name, settings=settings)
                        service.test_unc_write(service.connect(cluster.username, cno.password), unc, node.name)
                        ok.append(node.name)
                    except Exception as exc:  # noqa: BLE001
                        failed.append(f"{node.name}: {str(exc)[:150]}")
                if failed:
                    raise RuntimeError(f"ok: {', '.join(ok) or '–'}; fehlgeschlagen: {' | '.join(failed)}")
                return f"Schreibzugriff von {', '.join(ok)} (per CredSSP)"

            _soft_step(db, run.id, "access", "Zugriff von allen Knoten prüfen", _access, warnings, step_model=SmbCreateRunStep)

            def _inventory() -> str:
                note = ""
                try:
                    _discover_and_persist(db, netapp_cluster)
                except Exception as exc:  # noqa: BLE001
                    note = f"; NetApp-Discovery fehlgeschlagen: {exc}"
                exists = db.query(HyperVSmbShare).filter(
                    HyperVSmbShare.cluster_id == run.hyperv_cluster_id,
                    HyperVSmbShare.server.ilike(run.cifs_server), HyperVSmbShare.share.ilike(run.share_name),
                ).first()
                if exists is None:
                    db.add(HyperVSmbShare(cluster_id=run.hyperv_cluster_id, server=run.cifs_server, share=run.share_name))
                    db.commit()
                vhds = db.query(HyperVVhd).filter(HyperVVhd.cluster_id == run.hyperv_cluster_id).all()
                _refresh_smb_share_rows(db, run.hyperv_cluster_id, vhds)
                row = db.query(HyperVSmbShare).filter(
                    HyperVSmbShare.cluster_id == run.hyperv_cluster_id,
                    HyperVSmbShare.server.ilike(run.cifs_server), HyperVSmbShare.share.ilike(run.share_name),
                ).first()
                if row is None:
                    raise RuntimeError("Die neue Freigabe taucht im Inventory nicht auf" + note)
                if note:
                    raise RuntimeError(f"{_gb(row.capacity_bytes)}{note}")
                return f"{_gb(row.capacity_bytes)}, Volume {row.netapp_volume_name or '?'}"

            _soft_step(db, run.id, "inventory", "Inventory aktualisieren", _inventory, warnings, step_model=SmbCreateRunStep)

            group_id = opts.get("resource_group_id")
            if group_id:
                def _assign() -> str:
                    group = db.get(ResourceGroup, group_id)
                    if group is None:
                        raise RuntimeError("Protection Group nicht mehr vorhanden")
                    key = make_member_key(run.hyperv_cluster_id, _smb_share_key(run.cifs_server, run.share_name))
                    if key not in (group.members or []):
                        group.members = [*(group.members or []), key]
                        db.commit()
                    text = f"'{group.name}'"
                    if any(p.snapmirror_update for p in group.policies):
                        text += (
                            f" -- Hinweis: eine verknüpfte Policy repliziert per SnapMirror, für Volume "
                            f"{run.volume_name} gibt es aber noch keine SnapMirror-Beziehung"
                        )
                    return text

                _soft_step(db, run.id, "protection-group", "Protection Group zuordnen", _assign, warnings, step_model=SmbCreateRunStep)

            run.status = RestoreStatus.SUCCEEDED
            run.error_message = ("Mit Warnungen: " + " | ".join(warnings))[:2000] if warnings else None
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(
                db,
                f"SMB3-Freigabe {unc} angelegt ({_gb(run.volume_size_bytes)}, Volume {run.volume_name} auf {run.svm_name}, "
                f"Konten {', '.join(accounts)}) (durch {run.requested_by})"
                + (f" -- Warnungen: {' | '.join(warnings)}" if warnings else ""),
                level="WARNING" if warnings else "INFO",
            )
        except Exception as exc:
            db.rollback()
            run = db.get(SmbCreateRun, run_id)
            run.status = RestoreStatus.FAILED
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            run.error_message = str(detail)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, f"SMB3-Freigabe '{run.share_name}' anlegen fehlgeschlagen: {detail} (durch {run.requested_by})", level="ERROR")
    finally:
        db.close()


def _execute_rollback(run_id: str) -> None:
    db = SessionLocal()
    failures: list[str] = []
    try:
        run = db.get(SmbCreateRun, run_id)
        if run is None:
            return
        netapp_cluster = db.get(NetAppCluster, run.netapp_cluster_id)
        netapp = _netapp_service_for(netapp_cluster) if netapp_cluster else None

        def step(step_id: str, label: str, fn) -> None:
            with _StepCtx(db, run.id, step_id, label, step_model=SmbCreateRunStep) as ctx:
                try:
                    if netapp is None:
                        raise RuntimeError("NetApp-System nicht mehr vorhanden")
                    ctx.row.message = fn() or None
                except Exception as exc:  # noqa: BLE001
                    ctx.row.message = f"Fehlgeschlagen: {exc}"[:2000]
                    failures.append(f"{label}: {exc}")
            if ctx.row.message and ctx.row.message.startswith("Fehlgeschlagen:"):
                ctx.row.status = "error"
            db.commit()

        if run.share_created:
            def _share() -> str:
                netapp.delete_cifs_share(run.svm_name, run.share_name)
                run.share_created = False
                return f"Freigabe {run.share_name} gelöscht"

            step("rb-share", "Zurückrollen: Freigabe löschen", _share)

        if run.created_volume_uuid and not run.share_created:
            def _volume() -> str:
                netapp.delete_volume_forced(run.created_volume_uuid)
                run.created_volume_uuid = None
                return f"{run.volume_name} gelöscht"

            step("rb-volume", "Zurückrollen: Volume löschen", _volume)

        if netapp_cluster is not None and not failures:
            try:
                _discover_and_persist(db, netapp_cluster)
            except Exception:  # noqa: BLE001
                pass

        run = db.get(SmbCreateRun, run_id)
        run.finished_at = datetime.now(timezone.utc)
        if failures or run.has_created_objects:
            run.status = RestoreStatus.FAILED
            run.error_message = ("Zurückrollen unvollständig: " + " | ".join(failures or ["Objekte übrig"]))[:2000]
            db.commit()
            _log(db, f"SMB3-Freigabe '{run.share_name}' anlegen: {run.error_message}", level="ERROR")
        else:
            run.status = RestoreStatus.CLEANED_UP
            db.commit()
            _log(db, f"SMB3-Freigabe '{run.share_name}' anlegen: vollständig zurückgerollt")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(SmbCreateRun, run_id)
        if run is not None:
            run.status = RestoreStatus.FAILED
            run.error_message = f"Zurückrollen abgebrochen: {exc}"[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
