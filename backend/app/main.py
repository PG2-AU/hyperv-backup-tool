from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    alerts,
    auth,
    email_config,
    file_restore,
    hyperv_clusters,
    jobs,
    logs,
    netapp_clusters,
    resource_groups,
    restore,
    restore_infra,
    scheduler_config,
    schedules,
    search,
    settings as settings_routes,
    snapmirror_labels,
    storage,
    users,
    vms,
)
from app.core.config import get_settings
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.db.init_db import init_db
from app.db.session import SessionLocal


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


settings = get_settings()

app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(vms.router)
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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
