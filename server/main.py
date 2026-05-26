import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from services.sync_service import sync_loop
import routers.provision as provision_router
import routers.admin as admin_router
import routers.sync as sync_router

log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(sync_loop())
    log.info("Background sync loop started.")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Dashboard Tunnel Provisioning Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(provision_router.router, prefix="/api/provision", tags=["Provision"])
app.include_router(admin_router.router,     prefix="/api/provision", tags=["Admin"])
app.include_router(sync_router.router,      prefix="/api/sync",      tags=["Sync"])


@app.get("/health")
async def health():
    from database import get_server_id
    return {"status": "ok", "server_id": get_server_id()}
