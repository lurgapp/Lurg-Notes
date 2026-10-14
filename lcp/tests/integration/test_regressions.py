import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketState

from app.connection.context import ConnectionContext
from app.connection.handshake import HandshakeManager
from app.connection.manager import ConnectionManager
from app.health.routes import ready
from app.main import connect
from app.observability.metrics import Metrics
from app.protocol.constants import ConnectionState
from app.protocol.dispatcher import FrameDispatcher
from app.protocol.errors import LCPError
from app.protocol.models import HelloFrame, RequestFrame
from app.realtime.events import RedisEventBus
from app.realtime.manager import RealtimeManager
from app.realtime.replay import ReplayManager
from app.realtime.subscriptions import SubscriptionManager
from app.rpc.idempotency import IdempotencyManager, IdempotencyStatus
from app.security.auth import AuthManager
from app.security.rate_limit import RateLimiter
from app.session.manager import SessionManager
from app.session.resume import ResumeManager
from app.storage import keys


class Socket:
    application_state = WebSocketState.CONNECTED

    def __init__(self, block=False):
        self.sent, self.closed, self.block = [], None, block
        self.send_started = asyncio.Event()

    async def accept(self):
        pass

    async def send_text(self, value):
        self.send_started.set()
        if self.block:
            await asyncio.Event().wait()
        self.sent.append(json.loads(value))

    async def close(self, code, reason):
        self.closed = code
        self.application_state = WebSocketState.DISCONNECTED


class Router:
    def __init__(self):
        self.calls = 0
        self.result = {"ok": True, "payload": {"saved": True}}
        self.error = None

    async def route(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
async def rig(redis, settings, authority):
    connections, sessions = ConnectionManager(settings), SessionManager(redis, settings, authority)
    replay, subscriptions = ReplayManager(redis, settings), SubscriptionManager(redis)
    bus, metrics, router = RedisEventBus(redis, settings), Metrics(), Router()
    dispatcher = FrameDispatcher(
        connections=connections, sessions=sessions, replay=replay, subscriptions=subscriptions,
        handshake=HandshakeManager(ResumeManager(redis, sessions, settings), settings),
        auth=AuthManager(settings), limiter=RateLimiter(redis), idempotency=IdempotencyManager(redis),
        router=router, metrics=metrics,
    )
    realtime = RealtimeManager(bus, connections, replay, metrics, subscriptions, sessions)
    env = SimpleNamespace(**locals())
    yield env
    await bus.close()
    for context in list(connections.connections_by_id.values()):
        await connections.stop_delivery(context)
        await connections.unregister(context)


async def context(rig, *, user="usr", socket=None):
    session, token = (
        await rig.sessions.create_authenticated(
            user, rig.settings.lcp_gateway_id, security_version=1,
            auth_session_id=str(uuid4()), token_expires_at=int(time.time()) + rig.settings.lcp_session_ttl_seconds,
        )
        if user else await rig.sessions.create_guest(rig.settings.lcp_gateway_id)
    )
    ctx = ConnectionContext(
        f"conn_{session.id}", socket or Socket(), state=ConnectionState.READY,
        session_id=session.id, user_id=user, generation=1,
    )
    rig.connections.connections_by_id[ctx.connection_id] = ctx
    await rig.connections.bind_session(ctx)
    return ctx, token


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("invalidate", ["resume", "destroy", "expire"])
async def test_stale_or_expired_context_never_routes_rpc(rig, invalidate):
    ctx, token = await context(rig)
    if invalidate == "resume":
        await ResumeManager(rig.redis, rig.sessions, rig.settings).resume(token, 0, "other_gateway", ctx.session_id)
    elif invalidate == "destroy":
        await rig.sessions.destroy(ctx.session_id)
    else:
        await rig.redis.hset(keys.session(ctx.session_id), "expires_at", 1)
    with pytest.raises(LCPError):
        await rig.dispatcher.handle(ctx, RequestFrame(v=1, type="request", id="stale", method="nearby.events.list"))
    assert rig.router.calls == 0
    with pytest.raises(LCPError):
        await rig.dispatcher.handle(ctx, RequestFrame(v=1, type="request", id="logout", method="session.logout"))


async def test_logout_watch_rejects_a_replaced_generation(rig):
    ctx, token = await context(rig)
    await ResumeManager(rig.redis, rig.sessions, rig.settings).resume(token, 0, "other_gateway", ctx.session_id)
    with pytest.raises(LCPError):
        await rig.sessions.destroy(ctx.session_id, ctx.generation, rig.settings.lcp_gateway_id)
    assert (await rig.sessions.get(ctx.session_id)).generation == 2


async def test_offline_stream_event_replays_through_real_fanout(rig):
    ctx, token = await context(rig, user=None)
    subscription = await rig.subscriptions.subscribe(ctx, "nearby.events", {})
    await rig.connections.unregister(ctx)
    await rig.realtime.start()
    await rig.bus.publish("nearby.events", {"name": "updated", "id": "offline"})
    async with asyncio.timeout(3):
        while (await rig.sessions.get(ctx.session_id)).last_seq < 1:
            await asyncio.sleep(0.005)
    resumed = ConnectionContext("resumed", Socket())
    rig.connections.connections_by_id[resumed.connection_id] = resumed
    await rig.dispatcher.handle(resumed, HelloFrame(
        v=1, type="hello", resume_session_id=ctx.session_id, resume_token=token,
    ))
    events = [item for item in resumed.websocket.sent if item["type"] == "event"]
    assert len(events) == 1 and events[0]["payload"]["id"] == "offline"
    assert events[0]["subscription_id"] == subscription.id


async def test_live_event_cannot_overtake_or_duplicate_resume_replay(rig, monkeypatch):
    ctx, token = await context(rig, user=None)
    await rig.subscriptions.subscribe(ctx, "nearby.events", {})
    await rig.realtime.handle_event("nearby.events", {"name": "first"}, "source_1")
    await rig.connections.unregister(ctx)
    blocked, release = asyncio.Event(), asyncio.Event()
    original = rig.replay.get_after

    async def wait_before_read(*args, **kwargs):
        blocked.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(rig.replay, "get_after", wait_before_read)
    resumed = ConnectionContext("resumed", Socket())
    rig.connections.connections_by_id[resumed.connection_id] = resumed
    task = asyncio.create_task(rig.dispatcher.handle(resumed, HelloFrame(
        v=1, type="hello", resume_session_id=ctx.session_id, resume_token=token,
    )))
    await blocked.wait()
    await rig.realtime.handle_event("nearby.events", {"name": "second"}, "source_2")
    release.set()
    await task
    await asyncio.sleep(0.02)
    assert [item["seq"] for item in resumed.websocket.sent if item["type"] == "event"] == [1, 2]


async def test_slow_socket_does_not_block_fast_socket_or_ingress(rig):
    slow, _ = await context(rig, user="slow", socket=Socket(block=True))
    fast, _ = await context(rig, user="fast")
    for ctx in (slow, fast):
        await rig.subscriptions.subscribe(ctx, "nearby.events", {})
        rig.connections.start_delivery(ctx, rig.sessions, rig.replay, rig.metrics)
    await rig.realtime.start()
    await rig.bus.publish("nearby.events", {"name": "updated"})
    await wait_for(lambda: bool(fast.websocket.sent))
    # Redis roundtrips may outlast the intentionally tiny slow-socket timeout.
    # The invariant is independent fast delivery and bounded slow disconnection,
    # not which one is observed first on this test host.
    await wait_for(lambda: slow.websocket.closed is not None)
    assert slow.websocket.closed == 1013
    assert fast.state == ConnectionState.READY and not rig.bus._task.done()


async def test_stream_survives_consumer_error_and_recovers_pending_event(redis, settings):
    settings.lcp_legacy_pubsub_enabled = False
    bus = RedisEventBus(redis, settings)
    calls, completed = [], asyncio.Event()

    async def handler(topic, payload, event_id, owner_id=None):
        calls.append(event_id)
        if len(calls) == 1:
            raise OSError("failed fanout fixture")
        completed.set()

    # Persist before a worker exists: publication is retained across worker downtime.
    await bus.publish("nearby.events", {"name": "durable"})
    # Force pending retry eligibility without waiting 30 seconds in a unit test.
    original = redis.xautoclaim

    async def claim(*args, **kwargs):
        args = list(args)
        args[3] = 0
        return await original(*args, **kwargs)

    redis.xautoclaim = claim
    await bus.subscribe("nearby.events", handler)
    try:
        await asyncio.wait_for(completed.wait(), 3)
        assert len(calls) >= 2 and calls[0] == calls[1] and not bus._task.done()
    finally:
        await bus.close()


async def test_redelivered_stream_message_does_not_duplicate_even_after_ack(rig):
    ctx, _ = await context(rig)
    await rig.subscriptions.subscribe(ctx, "nearby.events", {})
    await rig.realtime.handle_event("nearby.events", {"name": "updated"}, "stable_source")
    assert await rig.replay.acknowledge(ctx.session_id, 1, 1)
    await rig.realtime.handle_event("nearby.events", {"name": "updated"}, "stable_source")
    assert (await rig.sessions.get(ctx.session_id)).last_seq == 1
    assert await rig.replay.get_after(ctx.session_id, 0) == []


async def test_subscriptions_inherit_expiry_and_clean_topic_index(rig):
    ctx, _ = await context(rig)
    await rig.subscriptions.subscribe(ctx, "nearby.events", {})
    assert 0 < await rig.redis.ttl(keys.subscriptions(ctx.session_id)) <= rig.settings.lcp_session_ttl_seconds
    await rig.redis.delete(keys.session(ctx.session_id))
    assert [item async for item in rig.subscriptions.for_topic("nearby.events")] == []
    assert await rig.redis.hlen(keys.topic_subscriptions("nearby.events")) == 0


async def test_retryable_error_and_exception_release_idempotency_lease(rig):
    ctx, _ = await context(rig)
    request = lambda ident: RequestFrame(
        v=1, type="request", id=ident, method="nearby.favorites.set", idempotency_key="same", payload={"id": "n1"}
    )
    rig.router.result = {"ok": False, "error": {"code": "UNAVAILABLE", "message": "Retry", "retryable": True}}
    await rig.dispatcher.handle(ctx, request("first"))
    await rig.dispatcher.handle(ctx, request("retry"))
    assert rig.router.calls == 2
    rig.router.error = OSError("failed adapter fixture")
    with pytest.raises(OSError):
        await rig.dispatcher.handle(ctx, request("exception"))
    assert not await rig.redis.exists(keys.idempotency(ctx.user_id, "nearby.favorites.set", "same"))


async def test_idempotency_fingerprint_and_short_fenced_lease(redis):
    manager = IdempotencyManager(redis, processing_seconds=30)
    first = await manager.acquire("user", "method", "key", {"id": "one"})
    assert first.status == IdempotencyStatus.NEW
    assert 0 < await redis.ttl(keys.idempotency("user", "method", "key")) <= 30
    with pytest.raises(LCPError, match="LCP_IDEMPOTENCY_CONFLICT"):
        await manager.acquire("user", "method", "key", {"id": "other"})
    await redis.expire(keys.idempotency("user", "method", "key"), 0)
    second = await manager.acquire("user", "method", "key", {"id": "one"})
    assert second.lease_id != first.lease_id
    assert not await manager.complete("user", "method", "key", {}, lease_id=first.lease_id)
    await manager.fail("user", "method", "key", lease_id=first.lease_id)
    assert await redis.exists(keys.idempotency("user", "method", "key"))


@pytest.mark.parametrize("hello", [False, True])
async def test_invalid_frames_do_not_reset_total_handshake_deadline(rig, hello):
    rig.settings.lcp_hello_timeout_seconds = 0.03
    rig.settings.lcp_auth_timeout_seconds = 0.03
    socket = Socket()
    socket.app = SimpleNamespace(state=SimpleNamespace(
        connections=rig.connections, dispatcher=rig.dispatcher, metrics=rig.metrics, settings=rig.settings,
    ))
    first = True

    async def receive_text():
        nonlocal first
        await asyncio.sleep(0.005)
        if first and hello:
            first = False
            return '{"v":1,"type":"hello"}'
        return "invalid JSON"

    socket.receive_text = receive_text
    await asyncio.wait_for(connect(socket), 0.3)
    assert socket.closed == 4408 and len(socket.sent) > 1


async def test_readiness_detects_dead_workers(redis):
    from fastapi import Response
    heartbeat = asyncio.get_running_loop().create_future()
    state = SimpleNamespace(ready=True, redis=redis, heartbeat=heartbeat, bus=SimpleNamespace(is_ready=False))
    request, response = SimpleNamespace(app=SimpleNamespace(state=state)), Response()
    assert (await ready(request, response))["dependency"] == "event_workers"
    assert response.status_code == 503
    state.bus.is_ready = True
    heartbeat.set_result(None)
    assert (await ready(request, Response()))["dependency"] == "event_workers"


async def test_expired_replay_is_trimmed_on_read_without_new_publication(rig):
    ctx, _ = await context(rig)
    await rig.subscriptions.subscribe(ctx, "nearby.events", {})
    await rig.realtime.handle_event("nearby.events", {"name": "old"}, "source_old")
    frame = (await rig.replay.get_after(ctx.session_id, 0))[0]
    serialized = frame.model_dump_json(exclude_none=True)
    await rig.redis.zadd(f"{keys.replay(ctx.session_id)}:age", {serialized: 1})
    assert not await rig.replay.can_replay_from(ctx.session_id, 0)
    assert await rig.replay.get_after(ctx.session_id, 0) == []


async def test_nearby_producer_uses_stream_format_only(redis, monkeypatch):
    import runpy
    import sys
    from pathlib import Path

    settings_stub = SimpleNamespace(lcp_event_bus_redis_url="redis://isolated-fixture")
    monkeypatch.setitem(sys.modules, "app.core.config", SimpleNamespace(settings=settings_stub))
    monkeypatch.setitem(sys.modules, "app.models.entities", SimpleNamespace(Event=object))
    producer_path = Path(__file__).resolve().parents[1] / "fixtures/nearby_lcp_events.py"
    namespace = runpy.run_path(str(producer_path))
    calls = []

    class ProducerRedis:
        async def xadd(self, stream, fields, **kwargs):
            calls.append((stream, fields))

        async def aclose(self):
            pass

    monkeypatch.setattr(namespace["Redis"], "from_url", lambda *args, **kwargs: ProducerRedis())
    event = SimpleNamespace(
        id="evt", category=SimpleNamespace(value="weather"), severity=2, latitude=1.5, longitude=2.5,
    )
    await namespace["publish_event"](event, "updated")
    assert calls[0][0] == "lcp:v1:event-stream" and calls[0][1]["topic"] == "nearby.events"
    assert json.loads(calls[0][1]["payload"])["event_id"] == "evt"


@pytest.mark.parametrize("topic", ["graph.events", "notes.events"])
async def test_private_event_fanout_is_owner_scoped_and_deduplicated(rig, topic):
    owner, _ = await context(rig, user="owner")
    other, _ = await context(rig, user="other")
    guest, _ = await context(rig, user=None)
    for ctx in (owner, other):
        await rig.subscriptions.subscribe(ctx, topic, {})
    with pytest.raises(ValueError):
        await rig.subscriptions.subscribe(guest, topic, {})
    with pytest.raises(ValueError):
        await rig.subscriptions.subscribe(owner, topic, {"owner_id": "other"})
    payload = {"name": "updated", "candidate_id": "private_candidate"}
    with pytest.raises(ValueError):
        await rig.realtime.handle_event(topic, payload, "missing_owner")
    await rig.realtime.handle_event(topic, payload, "stable_outbox_id", owner_id="owner")
    await rig.realtime.handle_event(topic, payload, "stable_outbox_id", owner_id="owner")
    assert len(await rig.replay.get_after(owner.session_id, 0)) == 1
    assert await rig.replay.get_after(other.session_id, 0) == []
    assert await rig.replay.get_after(guest.session_id, 0) == []


async def test_private_stream_envelope_preserves_owner_and_source_id(rig):
    ctx, _ = await context(rig, user="owner")
    await rig.subscriptions.subscribe(ctx, "graph.events", {})
    await rig.realtime.start()
    for _ in range(2):
        await rig.bus.publish(
            "graph.events", {"name": "graph.updated"}, owner_id="owner", source_event_id="outbox_uuid"
        )
    async with asyncio.timeout(3):
        while (await rig.sessions.get(ctx.session_id)).last_seq < 1:
            await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)
    assert (await rig.sessions.get(ctx.session_id)).last_seq == 1
    assert "owner_id" not in (await rig.replay.get_after(ctx.session_id, 0))[0].payload


async def test_short_session_subscription_does_not_expire_long_session_topic(rig):
    long, _ = await context(rig, user="long")
    await rig.subscriptions.subscribe(long, "nearby.events", {})
    short, _ = await context(rig, user="short")
    import time
    await rig.redis.hset(keys.session(short.session_id), "expires_at", int(time.time()) + 1)
    await rig.subscriptions.subscribe(short, "nearby.events", {})
    assert await rig.redis.ttl(keys.topic_subscriptions("nearby.events")) == -1
    await rig.redis.hset(keys.session(short.session_id), "expires_at", 1)
    await rig.realtime.handle_event("nearby.events", {"name": "updated"}, "new_source")
    assert len(await rig.replay.get_after(long.session_id, 0)) == 1
