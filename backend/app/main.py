from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.audit import AuditMiddleware

from app.api.routes import (
    ad_config as ad_config_routes,
    activities,
    reports,
    alerts,
    auth,
    capacity_history,
    config_transfer,
    csv_create,
    csv_delete,
    csv_resize,
    db_backup,
    email_config,
    file_restore,
    hyperv_clusters,
    jobs,
    kerberos_config as kerberos_config_routes,
    logs,
    netapp_clusters,
    resource_groups,
    restore,
    restore_infra,
    scheduler_config,
    schedules,
    search,
    settings as settings_routes,
    sites,
    smb_create,
    smb_delete,
    smb_resize,
    snapmirror_labels,
    storage,
    storage_access,
    users,
    vm_console,
    vm_create,
    vm_delete,
    vm_moves,
    vm_power,
    vms,
    winrm_certs,
)
from app.api.routes.hyperv_clusters import refresh_all_smb_share_rows
from app.core.config import get_settings
from app.core.kerberos_auth import ensure_ccache_env
from app.core.kerberos_config import ensure_krb5_config_env
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.db.init_db import init_db
from app.db.session import SessionLocal


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Muss VOR jedem Ticket-Erwerb gesetzt sein (siehe app.core.kerberos_auth) --
    # hier einmalig beim App-Start, unabhaengig davon, ob die krb5.conf
    # selbst schon existiert oder HVNB_WINRM_TRANSPORT ueberhaupt kerberos
    # ist (schadlos, falls nicht genutzt).
    ensure_krb5_config_env(get_settings())
    ensure_ccache_env(get_settings())
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    # Abgeleitete SMB3-Freigaben-Liste aus dem gespeicherten Stand neu
    # aufbauen (siehe refresh_all_smb_share_rows) -- best-effort, darf den
    # Start nie verhindern.
    db = SessionLocal()
    try:
        refresh_all_smb_share_rows(db)
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


settings = get_settings()

app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Aenderungsprotokoll fuer den Report "Audit-Trail" (Backlog #84)
app.add_middleware(AuditMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(vms.router)
app.include_router(activities.router)
app.include_router(reports.router)
app.include_router(storage.router)
app.include_router(netapp_clusters.router)
app.include_router(hyperv_clusters.router)
app.include_router(jobs.router)
app.include_router(resource_groups.router)
app.include_router(schedules.router)
app.include_router(snapmirror_labels.router)
app.include_router(logs.router)
app.include_router(search.router)
app.include_router(users.router)
app.include_router(settings_routes.router)
app.include_router(restore_infra.router)
app.include_router(restore.router)
app.include_router(file_restore.router)
app.include_router(email_config.router)
app.include_router(scheduler_config.router)
app.include_router(alerts.router)
app.include_router(storage_access.router)
app.include_router(winrm_certs.router)
app.include_router(kerberos_config_routes.router)
app.include_router(ad_config_routes.router)
app.include_router(capacity_history.router)
app.include_router(sites.router)
app.include_router(vm_moves.router)
app.include_router(vm_power.router)
app.include_router(vm_console.router)
app.include_router(vm_create.router)
app.include_router(vm_delete.router)
app.include_router(config_transfer.router)
app.include_router(db_backup.router)
app.include_router(csv_resize.router)
app.include_router(csv_create.router)
app.include_router(csv_delete.router)
app.include_router(smb_create.router)
app.include_router(smb_delete.router)
app.include_router(smb_resize.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
