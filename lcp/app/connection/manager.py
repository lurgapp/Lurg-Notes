import asyncio
import contextlib
import logging
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from ..config import Settings
from ..protocol.constants import ConnectionState
from ..protocol.errors import LCPError
from ..protocol.models import BaseFrame, frame_json
from .context import ConnectionContext


class ConnectionClosed(Exception):
    pass


class ConnectionManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.connections_by_session: dict[str, ConnectionContext] = {}
        self.connections_by_id: dict[str, ConnectionContext] = {}
        self.accepting = True
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> ConnectionContext:
        async with self._lock:
            if not self.accepting or len(self.connections_by_id) >= self.settings.lcp_max_connections:
                await websocket.close(code=1013, reason="Gateway connection capacity reached")
                raise RuntimeError("Gateway is draining or at capacity")
            await websocket.accept()
            now = asyncio.get_running_loop().time()
            context = ConnectionContext(
                connection_id=f"conn_{uuid4().hex}", websocket=websocket,
                handshake_deadline=now + self.settings.lcp_hello_timeout_seconds,
                connected_at=now, last_activity_at=now,
            )
            self.connections_by_id[context.connection_id] = context
            return context

    async def bind_session(self, context: ConnectionContext) -> None:
        if not context.session_id:
            return
        previous = self.connections_by_session.get(context.session_id)
        self.connections_by_session[context.session_id] = context
        if previous and previous is not context:
            await self.close(previous, 4409, "Session resumed elsewhere")

    async def unregister(self, context: ConnectionContext) -> None:
        if context.delivery_task and context.delivery_task is not asyncio.current_task():
            context.delivery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await context.delivery_task
        async with self._lock:
            self.connections_by_id.pop(context.connection_id, None)
            if context.session_id and self.connections_by_session.get(context.session_id) is context:
                self.connections_by_session.pop(context.session_id, None)
        context.state = ConnectionState.CLOSED

    async def send(self, context: ConnectionContext, frame: BaseFrame) -> None:
        async with context.send_lock:
            if context.state in {ConnectionState.CLOSING, ConnectionState.CLOSED}:
                raise ConnectionClosed()
            try:
                if context.websocket.application_state is not WebSocketState.CONNECTED:
                    raise ConnectionClosed()
                await asyncio.wait_for(
                    context.websocket.send_text(frame_json(frame)), self.settings.lcp_send_timeout_seconds
                )
            except (OSError, RuntimeError, TimeoutError, ConnectionClosed) as exc:
                await self.close(context, 1013, "Client send failed or timed out")
                raise ConnectionClosed() from exc

    async def close(self, context: ConnectionContext, code: int, reason: str) -> None:
        if context.state in {ConnectionState.CLOSING, ConnectionState.CLOSED}:
            return
        context.state = ConnectionState.CLOSING
        if context.delivery_task and context.delivery_task is not asyncio.current_task():
            context.delivery_task.cancel()
        if context.websocket.application_state is WebSocketState.CONNECTED:
            with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                await asyncio.wait_for(
                    context.websocket.close(code=code, reason=reason[:123]), self.settings.lcp_send_timeout_seconds
                )

    async def deliver_replay(self, context, sessions, replay, metrics) -> None:
        session = await sessions.require_current(
            context.session_id, context.generation, self.settings.lcp_gateway_id
        )
        if session.last_seq == context.last_sent_seq:
            await asyncio.sleep(self.settings.lcp_delivery_poll_seconds)
            return
        await sessions.require_current(
            context.session_id, context.generation, self.settings.lcp_gateway_id, force_authority=True
        )
        if not await replay.can_replay_from(context.session_id, context.last_sent_seq):
            raise LCPError("LCP_RESYNC_REQUIRED", "Replay retention exceeded", close_code=4410)
        for frame in await replay.get_after(
            context.session_id, context.last_sent_seq, count=self.settings.lcp_delivery_batch_size
        ):
            if frame.seq <= context.last_sent_seq:
                continue
            # A slow batch can outlive expiry, revocation or a resume elsewhere.
            # Check Redis ownership/expiry for every offer; authority keeps its
            # bounded short cache so this does not make one HTTP call per frame.
            await sessions.require_current(
                context.session_id, context.generation, self.settings.lcp_gateway_id
            )
            # Mark the offered sequence before yielding to transport IO: a fast client may ACK immediately.
            context.last_sent_seq = frame.seq
            await self.send(context, frame)
            metrics.inc("lcp_events_delivered_total")
    def start_delivery(self, context, sessions, replay, metrics) -> None:
        async def deliver():
            try:
                while context.state is ConnectionState.READY:
                    await self.deliver_replay(context, sessions, replay, metrics)
            except LCPError as exc:
                metrics.inc("lcp_delivery_errors_total")
                await self.close(context, exc.close_code or 1013, exc.code)
            except ConnectionClosed:
                metrics.inc("lcp_slow_or_broken_clients_total")
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("lcp").exception("lcp_connection_delivery_failed")
                metrics.inc("lcp_delivery_errors_total")
                await self.close(context, 1013, "Delivery temporarily unavailable")
            finally:
                if context.delivery_task is asyncio.current_task():
                    await self.unregister(context)
        context.delivery_task = asyncio.create_task(deliver(), name=f"lcp-delivery-{context.connection_id}")

    async def stop_delivery(self, context: ConnectionContext) -> None:
        task, context.delivery_task = context.delivery_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
