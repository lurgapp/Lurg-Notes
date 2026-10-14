import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest

from app.connection.context import ConnectionContext
from app.connection.handshake import HandshakeManager
from app.connection.manager import ConnectionManager
from app.observability.metrics import Metrics
from app.protocol.constants import ConnectionState
from app.protocol.dispatcher import FrameDispatcher
from app.protocol.errors import LCPError
from app.protocol.models import RequestFrame
from app.realtime.events import RedisEventBus
from app.realtime.manager import RealtimeManager
from app.realtime.replay import ReplayManager
from app.realtime.subscriptions import SubscriptionManager
from app.rpc.idempotency import IdempotencyManager
from app.security.auth import AuthManager
from app.security.rate_limit import RateLimiter
from app.session.manager import SessionManager
from app.session.models import ResumeStatus
from app.session.resume import ResumeManager
from app.storage import keys


@pytest.fixture
async def authority_http(settings):
    state = SimpleNamespace(active=True, status=200, calls=[])

    async def respond(request):
        assert request.url.path == "/v1/auth/internal/session-status"
        assert request.headers["X-Lurg-Internal-Token"] == settings.lcp_internal_token
        body = json.loads(request.content)
        assert set(body) == {"user_id", "security_version", "session_id", "token_expires_at"}
        state.calls.append(body)
        return httpx.Response(state.status, json={"active": state.active})

    auth = AuthManager(settings)
    auth._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    yield auth, state
    await auth.close()


def access_token(settings, **changes):
    claims = {
        "sub": str(uuid4()), "sid": str(uuid4()), "sv": 2, "type": "access",
        "iss": settings.auth_jwt_issuer, "aud": ["lurg", "notes"],
        "iat": int(time.time()), "exp": int(time.time()) + 300,
    } | changes
    return jwt.encode(claims, settings.auth_jwt_secret, algorithm="HS256"), claims


async def account_session(redis, settings, auth):
    token, claims = access_token(settings)
    principal = await auth.authenticate_access_token(token)
    sessions = SessionManager(redis, settings, auth)
    session, resume = await sessions.create_authenticated(
        principal.user_id, settings.lcp_gateway_id, security_version=principal.security_version,
        auth_session_id=principal.session_id, token_expires_at=principal.expires_at,
    )
    return sessions, session, resume, token, claims


async def test_identity_authority_is_checked_and_only_claims_are_persisted(redis, settings, authority_http):
    auth, state = authority_http
    sessions, session, _, token, claims = await account_session(redis, settings, auth)
    assert state.calls == [{
        "user_id": claims["sub"], "security_version": 2, "session_id": claims["sid"],
        "token_expires_at": claims["exp"],
    }]
    values = await redis.hgetall(keys.session(session.id))
    assert token not in json.dumps(values)
    assert values["security_version"] == "2" and values["auth_session_id"] == claims["sid"]
    assert session.expires_at == claims["exp"]
    assert 0 < await redis.ttl(keys.session(session.id)) <= 300
    await sessions.require_current(session.id, 1, settings.lcp_gateway_id)
    assert len(state.calls) == 1  # Idle polling has a bounded short cache.
    state.active = False
    with pytest.raises(LCPError, match="LCP_AUTH_INVALID"):
        await sessions.require_current(session.id, 1, settings.lcp_gateway_id, force_authority=True)
    assert len(state.calls) == 2


@pytest.mark.parametrize("missing", ["sid", "sv", "exp", "sub"])
async def test_legacy_or_incomplete_access_token_requires_reauthentication(settings, authority_http, missing):
    auth, state = authority_http
    _, claims = access_token(settings)
    claims.pop(missing)
    token = jwt.encode(claims, settings.auth_jwt_secret, algorithm="HS256")
    with pytest.raises(LCPError, match="LCP_AUTH_INVALID"):
        await auth.authenticate_access_token(token)
    assert state.calls == []


async def test_revoked_identity_cannot_rotate_resume_token(redis, settings, authority_http):
    auth, state = authority_http
    sessions, session, resume, _, _ = await account_session(redis, settings, auth)
    state.active = False
    result = await ResumeManager(redis, sessions, settings).resume(resume, 0, "next", session.id)
    assert result.status is ResumeStatus.REJECTED
    assert (await sessions.get(session.id)).generation == 1


async def test_legacy_transport_claims_and_expired_access_cannot_resume(redis, settings, authority_http):
    auth, state = authority_http
    sessions, session, resume, _, _ = await account_session(redis, settings, auth)
    await redis.hdel(keys.session(session.id), "auth_session_id", "security_version", "token_expires_at")
    result = await ResumeManager(redis, sessions, settings).resume(resume, 0, "next", session.id)
    assert result.status is ResumeStatus.REJECTED
    assert len(state.calls) == 1  # Reject missing claims before any authority call.
    await redis.hset(keys.session(session.id), mapping={
        "auth_session_id": str(uuid4()), "security_version": 2, "token_expires_at": 1,
    })
    with pytest.raises(LCPError, match="LCP_AUTH_INVALID"):
        await sessions.require_current(session.id, 1, settings.lcp_gateway_id)


async def test_private_fanout_drops_revoked_owner_and_outage_stays_pending(redis, settings, authority_http):
    auth, state = authority_http
    sessions, session, _, _, _ = await account_session(redis, settings, auth)
    subscriptions, replay = SubscriptionManager(redis), ReplayManager(redis, settings)
    context = SimpleNamespace(session_id=session.id, generation=1, subscriptions={})
    await subscriptions.subscribe(context, "graph.events", {})
    bus = RedisEventBus(redis, settings)
    realtime = RealtimeManager(bus, None, replay, Metrics(), subscriptions, sessions)
    state.status = 503
    with pytest.raises(LCPError, match="LCP_AUTH_UNAVAILABLE"):
        await realtime.handle_event("graph.events", {"name": "updated"}, "source", session.user_id)
    assert await replay.get_after(session.id, 0) == []
    assert await sessions.get(session.id) is not None
    state.status, state.active = 200, False
    await realtime.handle_event("graph.events", {"name": "updated"}, "source", session.user_id)
    assert await sessions.get(session.id) is None
    assert await redis.hlen(keys.topic_subscriptions("graph.events")) == 0


async def test_authority_failure_never_uses_an_old_active_cache(redis, settings, authority_http):
    auth, state = authority_http
    sessions, session, _, _, _ = await account_session(redis, settings, auth)
    state.status = 503
    with pytest.raises(LCPError, match="LCP_AUTH_UNAVAILABLE"):
        await sessions.require_current(session.id, 1, settings.lcp_gateway_id, force_authority=True)
    with pytest.raises(LCPError, match="LCP_AUTH_UNAVAILABLE"):
        await sessions.require_current(session.id, 1, settings.lcp_gateway_id)
    assert len(state.calls) == 3


async def test_revocation_is_fresh_before_rpc_and_queued_private_delivery(redis, settings, authority_http):
    auth, state = authority_http
    sessions, session, _, _, _ = await account_session(redis, settings, auth)
    connections = ConnectionManager(settings)
    replay, subscriptions, metrics = ReplayManager(redis, settings), SubscriptionManager(redis), Metrics()
    context = ConnectionContext(
        "authority_context", SimpleNamespace(sent=[]), state=ConnectionState.READY,
        session_id=session.id, user_id=session.user_id, generation=1,
    )
    bus = RedisEventBus(redis, settings)
    realtime = RealtimeManager(bus, connections, replay, metrics, subscriptions, sessions)
    await subscriptions.subscribe(context, "graph.events", {})
    await realtime.handle_event("graph.events", {"name": "updated"}, "before_revocation", session.user_id)
    assert len(await replay.get_after(session.id, 0)) == 1
    state.active = False
    with pytest.raises(LCPError, match="LCP_AUTH_INVALID"):
        await connections.deliver_replay(context, sessions, replay, metrics)
    assert context.websocket.sent == []

    class Adapter:
        calls = 0

        async def route(self, *args, **kwargs):
            self.calls += 1
            return {"ok": True, "payload": {}}

    adapter = Adapter()
    dispatcher = FrameDispatcher(
        connections=connections, sessions=sessions, auth=auth, replay=replay, subscriptions=subscriptions,
        handshake=HandshakeManager(ResumeManager(redis, sessions, settings), settings),
        limiter=RateLimiter(redis), idempotency=IdempotencyManager(redis), router=adapter, metrics=metrics,
    )
    with pytest.raises(LCPError, match="LCP_AUTH_INVALID"):
        await dispatcher.handle(context, RequestFrame(v=1, type="request", id="revoked", method="nearby.events.list"))
    assert adapter.calls == 0


@pytest.mark.parametrize("invalidated", ["revocation", "generation", "expiry"])
async def test_private_replay_stops_when_authority_changes_during_batch(
    redis, settings, authority_http, monkeypatch, invalidated,
):
    auth, state = authority_http
    if invalidated == "revocation":
        # Use a short real cache deadline so the first send can outlive it.
        settings.lcp_auth_check_cache_seconds = 0.01
    sessions, session, _, _, _ = await account_session(redis, settings, auth)
    connections, replay, metrics = ConnectionManager(settings), ReplayManager(redis, settings), Metrics()
    subscriptions = SubscriptionManager(redis)
    context = ConnectionContext(
        "batch_authority_context", SimpleNamespace(sent=[]), state=ConnectionState.READY,
        session_id=session.id, user_id=session.user_id, generation=1,
    )
    realtime = RealtimeManager(RedisEventBus(redis, settings), connections, replay, metrics, subscriptions, sessions)
    await subscriptions.subscribe(context, "graph.events", {})
    for index in range(2):
        await realtime.handle_event("graph.events", {"name": "updated"}, f"queued_{index}", session.user_id)

    async def send_and_invalidate(ctx, frame):
        ctx.websocket.sent.append(frame.seq)
        if invalidated == "revocation":
            state.active = False
            await asyncio.sleep(0.02)
        elif invalidated == "generation":
            await redis.hincrby(keys.session(session.id), "generation", 1)
        else:
            await redis.hset(keys.session(session.id), "expires_at", 1)

    monkeypatch.setattr(connections, "send", send_and_invalidate)
    authority_calls = len(state.calls)
    code = {"revocation": "LCP_AUTH_INVALID", "generation": "LCP_STALE_OWNER", "expiry": "LCP_SESSION_EXPIRED"}
    with pytest.raises(LCPError, match=code[invalidated]):
        await connections.deliver_replay(context, sessions, replay, metrics)
    assert context.websocket.sent == [1]
    assert context.last_sent_seq == 1
    if invalidated != "revocation":
        # Redis fencing and exact expiry still work with cached authority. The
        # per-frame guard must not turn into an HTTP call for every event.
        assert len(state.calls) == authority_calls + 1
