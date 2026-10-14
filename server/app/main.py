"""Standalone Lurg Notes API using the original shared service implementations.

The chat module provides Graph candidate helpers but its routes are not mounted.
The full platform's administration, connectors and support APIs are not mounted.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import current_user
from .config import settings
from .db import Session, engine, get_db
from .modules.app.releases import deliver_app_release_batch
from .modules.app.router import router as app_router
from .modules.graph.router import router as graph_router
from .modules.identity.central_router import router as central_identity_router
from .modules.identity.router import router as identity_router
from .modules.notes.e2ee import router as notes_e2ee_router
from .modules.notes.router import router as notes_router
from .modules.sync.outbox import run_outbox_publisher


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    stop = asyncio.Event()

    async def push_loop():
        while not stop.is_set():
            try:
                async with Session() as session:
                    await deliver_app_release_batch(session)
                app.state.push_checked_at = time.time()
                app.state.push_healthy = True
            except Exception as error:
                app.state.push_checked_at = time.time()
                app.state.push_healthy = False
                logging.getLogger(__name__).error("push delivery poll failed (%s)", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    app.state.push_checked_at = 0.0
    app.state.push_healthy = False
    push_task = asyncio.create_task(push_loop(), name="notes-release-push")
    outbox_task = asyncio.create_task(run_outbox_publisher(), name="notes-outbox")
    try:
        yield
    finally:
        stop.set()
        outbox_task.cancel()
        with suppress(asyncio.CancelledError):
            await outbox_task
        try:
            await asyncio.wait_for(push_task, timeout=10)
        except asyncio.TimeoutError:
            push_task.cancel()
            with suppress(asyncio.CancelledError):
                await push_task


app = FastAPI(
    title="Lurg Notes API",
    summary="Notes, trusted-device E2EE, Lurg ID, Graph and app release delivery.",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_origin_regex=r"https://([a-z0-9-]+\.)?lurg\.pro",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-CSRF-Token", "X-Lurg-App-Name", "X-Lurg-Notes-Device-Id", "X-Lurg-Notes-Device-Credential"],
)

# Preserve the shared maintenance control and request behavior. The control
# table is provided by the retained migration chain; no admin API is exposed.
_maintenance = {"enabled": False, "expires": 0.0}
_maintenance_lock = asyncio.Lock()


async def maintenance_enabled():
    if time.monotonic() < _maintenance["expires"]:
        return _maintenance["enabled"]
    async with _maintenance_lock:
        if time.monotonic() < _maintenance["expires"]:
            return _maintenance["enabled"]
        try:
            async with asyncio.timeout(1.5):
                async with Session() as session:
                    enabled = bool(await session.scalar(text("SELECT enabled FROM operations_controls WHERE key='maintenance_mode'")))
            _maintenance.update(enabled=enabled, expires=time.monotonic() + 2)
        except Exception:
            _maintenance["expires"] = time.monotonic() + .5
        return _maintenance["enabled"]


@app.middleware("http")
async def api_robots_header(request: Request, call_next):
    if request.method != "OPTIONS" and not request.url.path.startswith(("/health", "/ready", "/docs", "/openapi.json")):
        if await maintenance_enabled():
            return Response(content='{"detail":"Проводятся технические работы"}', status_code=503, media_type="application/json", headers={"Retry-After": "30"})
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


app.include_router(identity_router)
app.include_router(central_identity_router)
app.include_router(graph_router)
app.include_router(notes_router)
app.include_router(notes_e2ee_router)
app.include_router(app_router)


@app.get("/", tags=["Core"])
async def api_root():
    return {"name": "Lurg Notes API", "version": "0.2.0", "documentation": "/docs", "domains": ["identity", "graph", "notes", "app"]}


@app.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nDisallow: /\n"


@app.get("/health", tags=["Core"])
async def health():
    return {"status": "ok"}


@app.get("/ready", tags=["Core"])
async def ready():
    try:
        async with asyncio.timeout(1.5):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception:
        return Response(content='{"status":"unavailable"}', status_code=503, media_type="application/json")
    return {"status": "ready"}


@app.get("/profile/status", tags=["Core"])
async def profile_status(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(text("""SELECT u.id,u.email,u.name,u.created_at,
      COALESCE(u.onboarding_completed,false) onboarding_completed,
      COALESCE(u.preferences,'{}'::jsonb) preferences,
      (SELECT count(*) FROM nodes n WHERE n.user_id=u.id AND n.lifecycle_status='active') node_count
      FROM users u WHERE u.id=:uid"""), {"uid": user_id})).mappings().one()
    node_count = int(row["node_count"] or 0)
    preferences = {"theme": "dark", "language": "ru", "notifications_enabled": True, "auto_use_graph": True, "allow_web_search": True, "haptics_enabled": True, **(row["preferences"] or {})}
    onboarding_completed = bool(row["onboarding_completed"]) or node_count > 0
    return {
        "user_id": str(row["id"]), "id": str(row["id"]), "email": row["email"],
        "name": row["name"], "display_name": row["name"],
        "account_created_at": row["created_at"].isoformat(), "created_at": row["created_at"].isoformat(),
        "is_new_user": not onboarding_completed and node_count == 0,
        "onboarding_completed": onboarding_completed, "graph_initialized": node_count > 0,
        "graph_node_count": node_count, "node_count": node_count, "preferences": preferences,
    }


@app.get("/core/capabilities", tags=["Core"])
async def capabilities():
    return {
        "platform": "Lurg Notes", "modules": {
            "identity": "core-local-v1", "graph": "domain-v1", "graph.memory": "v1",
            "graph.permission": "owner-only-v1", "notes": "v1", "notes.e2ee": "v2",
            "ai.provider": f"{settings.ai_provider}-v1",
            "search.provider": settings.search_provider if settings.web_search_enabled else "disabled",
            "sync": "outbox-v1",
        },
    }
