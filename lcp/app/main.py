import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketDisconnect

from .config import get_settings
from .connection.handshake import HandshakeManager
from .connection.manager import ConnectionClosed, ConnectionManager
from .health.routes import router as health_router
from .observability.logging import configure_logging
from .observability.metrics import Metrics
from .protocol.constants import ConnectionState
from .protocol.dispatcher import FrameDispatcher
from .protocol.errors import LCPError
from .protocol.models import ErrorFrame, ErrorPayload
from .protocol.parser import parse_frame
from .realtime.events import RedisEventBus
from .realtime.manager import RealtimeManager
from .realtime.replay import ReplayManager
from .realtime.subscriptions import SubscriptionManager
from .rpc.client import RPCClient
from .rpc.idempotency import IdempotencyManager
from .rpc.router import ServiceRouter
from .security.auth import AuthManager
from .security.rate_limit import RateLimiter
from .session.manager import SessionManager
from .session.resume import ResumeManager
from .storage import keys
from .storage.redis import create_redis


async def gateway_heartbeat(app: FastAPI) -> None:
    settings = app.state.settings
    while True:
        await app.state.redis.set(
            keys.gateway(settings.lcp_gateway_id), "alive", ex=settings.lcp_heartbeat_seconds * 3
        )
        await asyncio.sleep(settings.lcp_heartbeat_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging()
    redis = create_redis(settings.redis_url)
    if not settings.auth_jwt_secret or not settings.lcp_internal_token:
        raise RuntimeError("AUTH_JWT_SECRET and LCP_INTERNAL_TOKEN must be configured")
    connections, metrics = ConnectionManager(settings), Metrics()
    auth = AuthManager(settings)
    sessions = SessionManager(redis, settings, authority=auth)
    replay, subscriptions = ReplayManager(redis, settings), SubscriptionManager(redis, settings.lcp_max_subscriptions)
    bus = RedisEventBus(redis, settings)
    client = RPCClient()
    dispatcher = FrameDispatcher(
        connections=connections,
        handshake=HandshakeManager(ResumeManager(redis, sessions, settings), settings),
        sessions=sessions,
        auth=auth,
        router=ServiceRouter(client, settings),
        limiter=RateLimiter(redis),
        idempotency=IdempotencyManager(redis, processing_seconds=settings.lcp_idempotency_processing_seconds),
        subscriptions=subscriptions,
        replay=replay,
        metrics=metrics,
    )
    app.state.settings, app.state.redis, app.state.connections, app.state.metrics = (
        settings,
        redis,
        connections,
        metrics,
    )
    app.state.dispatcher, app.state.bus, app.state.ready = dispatcher, bus, False
    await redis.ping()
    await RealtimeManager(bus, connections, replay, metrics, subscriptions, sessions).start()
    heartbeat = asyncio.create_task(gateway_heartbeat(app), name="lcp-gateway-heartbeat")
    app.state.heartbeat = heartbeat
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        connections.accepting = False
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        for context in list(connections.connections_by_id.values()):
            await connections.close(context, 1012, "Service restart")
        await bus.close()
        await client.close()
        await auth.close()
        await redis.aclose()


app = FastAPI(title="LCP Gateway", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(health_router)


@app.get("/", include_in_schema=False)
async def site() -> FileResponse:
    return FileResponse("app/site/index.html")


@app.websocket("/connect")
async def connect(websocket: WebSocket) -> None:
    connections: ConnectionManager = websocket.app.state.connections
    dispatcher: FrameDispatcher = websocket.app.state.dispatcher
    metrics: Metrics = websocket.app.state.metrics
    try:
        context = await connections.register(websocket)
    except RuntimeError:
        return
    metrics.inc("lcp_connections_total")
    metrics.set_connections(len(connections.connections_by_id))
    try:
        while True:
            try:
                async with asyncio.timeout_at(context.handshake_deadline):
                    raw = await websocket.receive_text()
            except TimeoutError:
                await connections.close(context, 4408, "LCP_HANDSHAKE_TIMEOUT")
                return
            context.last_activity_at = asyncio.get_running_loop().time()
            frame = None
            try:
                async with asyncio.timeout_at(context.handshake_deadline):
                    frame = await parse_frame(raw)
                    await dispatcher.handle(context, frame)
                # A handler such as session.logout may send a terminal response
                # and close the transport itself.  Do not attempt another
                # receive on an already closed ASGI WebSocket.
                if context.state in {ConnectionState.CLOSING, ConnectionState.CLOSED}:
                    return
            except TimeoutError:
                await connections.close(context, 4408, "LCP_HANDSHAKE_TIMEOUT")
                return
            except LCPError as exc:
                metrics.inc("lcp_protocol_errors_total")
                await connections.send(
                    context,
                    ErrorFrame(
                        v=websocket.app.state.settings.lcp_protocol_version,
                        id=getattr(locals().get("frame", None), "id", None),
                        error=ErrorPayload(
                            code=exc.code,
                            message=exc.message,
                            retryable=exc.retryable,
                            retry_after_ms=exc.retry_after_ms,
                            details=exc.details,
                        ),
                    ),
                )
                if exc.close_code:
                    await connections.close(context, exc.close_code, exc.code)
                    return
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    finally:
        await connections.unregister(context)
        metrics.set_connections(len(connections.connections_by_id))
