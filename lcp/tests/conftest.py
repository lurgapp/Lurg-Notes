import os
from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.storage import keys


@pytest.fixture
def settings():
    return Settings(
        lcp_gateway_id="gw_test", lcp_session_ttl_seconds=3600, lcp_replay_max_events=10,
        lcp_replay_max_age_seconds=600, auth_jwt_secret="isolated-test-key-with-at-least-32-bytes", lcp_internal_token="internal",
        lcp_delivery_poll_seconds=0.005, lcp_send_timeout_seconds=0.05, lcp_legacy_pubsub_enabled=False,
    )


@pytest.fixture
def authority():
    # Transport unit tests isolate central identity; authority behavior has its
    # own HTTP-contract regressions and real backend/PostgreSQL verification.
    async def active(session, *, force=False):
        return None
    return SimpleNamespace(validate_session=active)


@pytest.fixture
async def redis(monkeypatch):
    # Tests never flush Redis and never operate on the production LCP namespace.
    # Each fixture has an independent random namespace, including stream/group/legacy lease.
    prefix = f"lcp-test:{uuid4().hex}:"
    for name in (
        "SESSION", "REPLAY", "SUBSCRIPTIONS", "IDEMPOTENCY", "RATE_LIMIT", "GATEWAY",
        "TOPIC_SUBSCRIPTIONS", "REPLAY_SEEN", "SUBSCRIPTIONS_PREFIX", "EVENT_STREAM", "LEGACY_LEADER",
    ):
        monkeypatch.setattr(keys, name, getattr(keys, name).replace("lcp:v1:", prefix))
    monkeypatch.setattr(keys, "EVENT_GROUP", f"{prefix}fanout")
    url = os.environ.get("LCP_TEST_REDIS_URL")
    client = Redis.from_url(url, decode_responses=True) if url else fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    owned_keys = [key async for key in client.scan_iter(match=f"{prefix}*")]
    if owned_keys:
        await client.delete(*owned_keys)
    await client.aclose()
