import asyncio
import logging

from ..connection.context import ConnectionContext
from ..connection.handshake import HandshakeManager
from ..connection.manager import ConnectionManager
from ..observability.metrics import Metrics
from ..observability.tracing import timed
from ..realtime.replay import ReplayManager
from ..realtime.subscriptions import SubscriptionManager
from ..rpc.idempotency import IdempotencyManager, IdempotencyStatus
from ..rpc.methods import metadata_for
from ..rpc.router import PrincipalContext, ServiceRouter
from ..security.auth import AuthManager, Principal
from ..security.permissions import require_account
from ..security.rate_limit import RateLimiter
from ..session.manager import SessionManager
from .constants import ConnectionState
from .errors import LCPError
from .models import AckFrame, BaseFrame, ErrorFrame, ErrorPayload, HelloFrame, RequestFrame, ResponseFrame
from .validator import validate_frame_for_state


class FrameDispatcher:
    def __init__(
        self,
        *,
        connections: ConnectionManager,
        handshake: HandshakeManager,
        sessions: SessionManager,
        auth: AuthManager,
        router: ServiceRouter,
        limiter: RateLimiter,
        idempotency: IdempotencyManager,
        subscriptions: SubscriptionManager,
        replay: ReplayManager,
        metrics: Metrics,
    ) -> None:
        self.connections, self.handshake, self.sessions, self.auth, self.router = (
            connections,
            handshake,
            sessions,
            auth,
            router,
        )
        self.limiter, self.idempotency, self.subscriptions, self.replay, self.metrics = (
            limiter,
            idempotency,
            subscriptions,
            replay,
            metrics,
        )
        self.connections.settings = self.handshake.settings
        self.log = logging.getLogger("lcp")

    async def handle(self, context: ConnectionContext, frame: BaseFrame) -> None:
        validate_frame_for_state(frame, context.state)
        if isinstance(frame, HelloFrame):
            await self._hello(context, frame)
        elif isinstance(frame, AckFrame):
            await self._ack(context, frame)
        elif isinstance(frame, RequestFrame):
            await self._request(context, frame)

    async def _hello(self, context: ConnectionContext, frame: HelloFrame) -> None:
        ack = await self.handshake.handle_hello(context, frame)
        await self.connections.send(context, ack)
        if context.state is ConnectionState.READY:
            assert context.session_id
            session = await self.sessions.require_current(
                context.session_id, context.generation, self.handshake.settings.lcp_gateway_id
            )
            await self.connections.bind_session(context)
            restored = await self.subscriptions.restore(context.session_id)
            context.subscriptions = {item.id: item for item in restored}
            context.last_sent_seq = (
                max(frame.last_acked_seq, session.last_acked_seq) if ack.resume == "resumed" else session.last_seq
            )
            if ack.resume == "resumed":
                await self.connections.deliver_replay(context, self.sessions, self.replay, self.metrics)
            self.connections.start_delivery(context, self.sessions, self.replay, self.metrics)
            self.metrics.inc(f"lcp_resume_{ack.resume}_total")

    async def _ack(self, context: ConnectionContext, frame: AckFrame) -> None:
        if not context.session_id or context.generation is None:
            raise LCPError("LCP_INVALID_STATE", "No session to acknowledge")
        await self.sessions.require_current(
            context.session_id, context.generation, self.handshake.settings.lcp_gateway_id
        )
        if frame.seq > context.last_sent_seq:
            raise LCPError("LCP_BAD_ACK", "Cannot acknowledge an event not delivered to this connection")
        if not await self.replay.acknowledge(context.session_id, context.generation, frame.seq):
            raise LCPError("LCP_STALE_OWNER", "Session ownership changed", close_code=4409)

    async def _request(self, context: ConnectionContext, frame: RequestFrame) -> None:
        if frame.id in context.pending_requests:
            raise LCPError("LCP_DUPLICATE_REQUEST", "Request id is already in flight")
        if len(context.pending_requests) >= self.handshake.settings.lcp_max_inflight_requests:
            raise LCPError(
                "LCP_TOO_MANY_REQUESTS", "Too many requests in flight", retryable=True, retry_after_ms=100
            )
        future = asyncio.get_running_loop().create_future()
        context.pending_requests[frame.id] = future
        try:
            async with timed() as elapsed:
                if context.state is ConnectionState.AUTHENTICATING:
                    await self._authenticate(context, frame)
                else:
                    await self._ready_request(context, frame)
                duration_ms = elapsed()
                self.metrics.observe_rpc(duration_ms / 1000)
                self.log.info(
                    "lcp_rpc_completed",
                    extra={
                        "connection_id": context.connection_id,
                        "session_id": context.session_id,
                        "request_id": frame.id,
                        "method": frame.method,
                        "duration_ms": duration_ms,
                        "status": "ok",
                    },
                )
        finally:
            context.pending_requests.pop(frame.id, None)

    async def _authenticate(self, context: ConnectionContext, frame: RequestFrame) -> None:
        if frame.method == "session.authenticate":
            token = frame.payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise LCPError("LCP_AUTH_INVALID", "access_token is required")
            principal = await self.auth.authenticate_access_token(token)
            session, resume_token = await self.sessions.create_authenticated(
                principal.user_id or "", self.handshake.settings.lcp_gateway_id,
                security_version=principal.security_version, auth_session_id=principal.session_id,
                token_expires_at=principal.expires_at,
            )
        elif frame.method == "session.guest":
            session, resume_token = await self.sessions.create_guest(self.handshake.settings.lcp_gateway_id)
        else:
            raise LCPError("LCP_INVALID_STATE", "Authenticate first")
        context.session_id, context.user_id, context.generation = (
            session.id,
            session.user_id,
            session.generation,
        )
        context.resume_token, context.state = resume_token, ConnectionState.READY
        context.handshake_deadline = None
        await self.connections.bind_session(context)
        self.metrics.inc("lcp_sessions_created_total")
        await self.connections.send(
            context,
            ResponseFrame(
                v=1,
                id=frame.id,
                payload={
                    "session_id": session.id,
                    "auth_kind": session.auth_kind,
                    "resume_token": resume_token,
                    "generation": session.generation,
                    "expires_at": session.expires_at,
                },
            ),
        )

        self.connections.start_delivery(context, self.sessions, self.replay, self.metrics)

    async def _ready_request(self, context: ConnectionContext, frame: RequestFrame) -> bool | None:
        if not context.session_id or context.generation is None:
            raise LCPError("LCP_INVALID_STATE", "Session unavailable")
        session = await self.sessions.require_current(
            context.session_id, context.generation, self.handshake.settings.lcp_gateway_id, force_authority=True
        )
        if session.user_id != context.user_id:
            raise LCPError("LCP_STALE_OWNER", "Session identity changed", close_code=4409)
        metadata = metadata_for(frame.method)
        if not metadata:
            raise LCPError("LCP_METHOD_NOT_FOUND", "Unknown method")
        principal = Principal(context.user_id, "account" if context.user_id else "guest")
        if metadata.auth_required:
            require_account(principal)
        rate = await self.limiter.check(context.user_id or f"guest:{context.session_id}", frame.method)
        if not rate.allowed:
            raise LCPError(
                "LCP_RATE_LIMITED", "Rate limit exceeded", retryable=True, retry_after_ms=rate.retry_after_ms
            )
        if frame.method == "realtime.subscribe":
            await self._subscribe(context, frame)
            return
        if frame.method == "realtime.unsubscribe":
            await self._unsubscribe(context, frame)
            return
        if frame.method == "session.logout":
            await self._logout(context, frame)
            return
        if not context.session_id:
            raise LCPError("LCP_INVALID_STATE", "Session unavailable")
        idem = None
        if metadata.idempotency:
            if not frame.idempotency_key:
                raise LCPError("LCP_IDEMPOTENCY_REQUIRED", "idempotency_key is required")
            idem = await self.idempotency.acquire(
                context.user_id or context.session_id, frame.method, frame.idempotency_key, frame.payload
            )
            if idem.status is IdempotencyStatus.PROCESSING:
                raise LCPError(
                    "LCP_IDEMPOTENCY_IN_PROGRESS",
                    "Request is already processing",
                    retryable=True,
                    retry_after_ms=250,
                )
            if idem.status is IdempotencyStatus.COMPLETED:
                await self._send_service_result(
                    context,
                    frame.id,
                    {"ok": not idem.is_error, "payload": idem.payload}
                    if not idem.is_error
                    else {"ok": False, "error": idem.payload},
                )
                return
        try:
            result = await self.router.route(
                frame.method,
                PrincipalContext(context.user_id, context.session_id, principal.kind),
                frame.payload,
                metadata,
                frame.id,
                idempotency_key=frame.idempotency_key,
                generation=context.generation,
                gateway_id=self.handshake.settings.lcp_gateway_id,
            )
        except BaseException:
            if idem and frame.idempotency_key:
                await asyncio.shield(self.idempotency.fail(
                    context.user_id or context.session_id, frame.method, frame.idempotency_key,
                    lease_id=idem.lease_id,
                ))
            raise
        if idem and frame.idempotency_key:
            await self.idempotency.complete(
                context.user_id or context.session_id,
                frame.method,
                frame.idempotency_key,
                result.get("payload") if result.get("ok") else result.get("error", {}),
                is_error=not result.get("ok"),
                lease_id=idem.lease_id,
            )
        await self._send_service_result(context, frame.id, result)
        self.metrics.inc("lcp_rpc_total")
        self.metrics.inc("lcp_rpc_success_total" if result.get("ok") else "lcp_rpc_error_total")
        return bool(result.get("ok"))

    async def _subscribe(self, context: ConnectionContext, frame: RequestFrame) -> None:
        topic, filter = frame.payload.get("topic"), frame.payload.get("filter", {})
        if not isinstance(topic, str) or not isinstance(filter, dict):
            raise LCPError("LCP_BAD_REQUEST", "topic and filter are required")
        if topic in self.subscriptions.PRIVATE_TOPICS:
            require_account(Principal(context.user_id, "account" if context.user_id else "guest"))
        try:
            subscription = await self.subscriptions.subscribe(context, topic, filter)
        except ValueError as exc:
            raise LCPError("LCP_BAD_REQUEST", str(exc)) from exc
        await self.connections.send(
            context, ResponseFrame(v=1, id=frame.id, payload={"subscription_id": subscription.id})
        )

    async def _unsubscribe(self, context: ConnectionContext, frame: RequestFrame) -> None:
        subscription_id = frame.payload.get("subscription_id")
        if not isinstance(subscription_id, str):
            raise LCPError("LCP_BAD_REQUEST", "subscription_id is required")
        await self.subscriptions.unsubscribe(context, subscription_id)
        await self.connections.send(
            context, ResponseFrame(v=1, id=frame.id, payload={"subscription_id": subscription_id})
        )

    async def _logout(self, context: ConnectionContext, frame: RequestFrame) -> None:
        if not context.session_id:
            raise LCPError("LCP_INVALID_STATE", "Session unavailable")
        await self.connections.stop_delivery(context)
        await self.sessions.destroy(
            context.session_id, context.generation, self.handshake.settings.lcp_gateway_id
        )
        await self.connections.send(context, ResponseFrame(v=1, id=frame.id, payload={"logged_out": True}))
        await self.connections.close(context, 1000, "Logged out")

    async def _send_service_result(self, context: ConnectionContext, request_id: str, result: dict) -> None:
        if result.get("ok"):
            await self.connections.send(
                context, ResponseFrame(v=1, id=request_id, payload=result.get("payload", {}))
            )
            return
        error = result.get("error", {})
        await self.connections.send(
            context,
            ErrorFrame(
                v=1,
                id=request_id,
                error=ErrorPayload(
                    code=str(error.get("code", "LCP_SERVICE_ERROR")),
                    message=str(error.get("message", "Service request failed")),
                    retryable=bool(error.get("retryable", False)),
                    retry_after_ms=error.get("retry_after_ms"),
                    details=error.get("details"),
                ),
            ),
        )
