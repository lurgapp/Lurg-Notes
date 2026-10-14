import json

from starlette.websockets import WebSocketState

from app.connection.context import ConnectionContext
from app.connection.handshake import HandshakeManager
from app.connection.manager import ConnectionManager
from app.observability.metrics import Metrics
from app.protocol.dispatcher import FrameDispatcher
from app.protocol.models import EventFrame, HelloFrame, RequestFrame
from app.realtime.replay import ReplayManager
from app.realtime.subscriptions import SubscriptionManager
from app.rpc.client import RPCClient
from app.rpc.idempotency import IdempotencyManager
from app.rpc.router import ServiceRouter
from app.security.auth import AuthManager
from app.security.rate_limit import RateLimiter
from app.session.manager import SessionManager
from app.session.resume import ResumeManager


class FakeSocket:
    application_state = WebSocketState.CONNECTED

    def __init__(self):
        self.sent = []

    async def send_text(self, value):
        self.sent.append(json.loads(value))

    async def close(self, code, reason):
        self.application_state = WebSocketState.DISCONNECTED


async def test_guest_subscribe_disconnect_resume_and_replay(redis, settings):
    connections, sessions = ConnectionManager(), SessionManager(redis, settings)
    replay, subscriptions = ReplayManager(redis, settings), SubscriptionManager(redis)
    client = RPCClient()
    dispatcher = FrameDispatcher(
        connections=connections,
        handshake=HandshakeManager(ResumeManager(redis, sessions, settings), settings),
        sessions=sessions,
        auth=AuthManager(settings),
        router=ServiceRouter(client, settings),
        limiter=RateLimiter(redis),
        idempotency=IdempotencyManager(redis),
        subscriptions=subscriptions,
        replay=replay,
        metrics=Metrics(),
    )
    socket = FakeSocket()
    ctx = ConnectionContext("conn_1", socket)
    connections.connections_by_id[ctx.connection_id] = ctx
    await dispatcher.handle(ctx, HelloFrame(v=1, type="hello"))
    await dispatcher.handle(
        ctx, RequestFrame(v=1, type="request", id="auth", method="session.guest", payload={})
    )
    session_data = socket.sent[-1]["payload"]
    await dispatcher.handle(
        ctx,
        RequestFrame(
            v=1,
            type="request",
            id="sub",
            method="realtime.subscribe",
            payload={"topic": "nearby.events", "filter": {}},
        ),
    )
    subscription_id = socket.sent[-1]["payload"]["subscription_id"]
    await connections.unregister(ctx)
    event = EventFrame(
        v=1, id="evt_1", seq=1, subscription_id=subscription_id, name="updated", payload={"id": "n1"}
    )
    await replay.append(session_data["session_id"], 1, event)
    resumed_socket = FakeSocket()
    resumed = ConnectionContext("conn_2", resumed_socket)
    connections.connections_by_id[resumed.connection_id] = resumed
    await dispatcher.handle(
        resumed,
        HelloFrame(
            v=1,
            type="hello",
            resume_session_id=session_data["session_id"],
            resume_token=session_data["resume_token"],
            last_acked_seq=0,
        ),
    )
    assert resumed_socket.sent[0]["type"] == "hello_ack"
    assert resumed_socket.sent[0]["resume"] == "resumed"
    assert resumed_socket.sent[1] == {
        "v": 1,
        "type": "event",
        "id": "evt_1",
        "seq": 1,
        "subscription_id": subscription_id,
        "name": "updated",
        "payload": {"id": "n1"},
    }
    await client.close()
