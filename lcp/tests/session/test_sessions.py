import time
from uuid import uuid4

from app.protocol.models import EventFrame
from app.realtime.replay import ReplayManager
from app.session.manager import SessionManager
from app.session.models import ResumeStatus
from app.session.resume import ResumeManager
from app.session.tokens import generate_resume_token, hash_resume_token
from app.storage import keys


def test_resume_tokens_are_random_and_hashed():
    raw, digest = generate_resume_token()
    assert raw.startswith("lcp_rt_")
    assert digest == hash_resume_token(raw)
    assert raw not in digest


async def test_session_creation_and_resume_token_rotation(redis, settings, authority):
    sessions = SessionManager(redis, settings, authority)
    session, token = await sessions.create_authenticated(
        "usr_1", "gw_test", security_version=1, auth_session_id=str(uuid4()),
        token_expires_at=int(time.time()) + 3600,
    )
    stored = await redis.hget(keys.session(session.id), "resume_token_hash")
    assert stored == hash_resume_token(token)
    resumed = await ResumeManager(redis, sessions, settings).resume(token, 0, "gw_next", session.id)
    assert resumed.status is ResumeStatus.RESUMED
    assert resumed.session and resumed.session.generation == 2
    assert resumed.raw_token and resumed.raw_token != token
    assert await redis.hget(keys.session(session.id), "resume_token_hash") == hash_resume_token(
        resumed.raw_token
    )


async def test_stale_generation_and_ack_are_rejected(redis, settings):
    sessions = SessionManager(redis, settings)
    session, _ = await sessions.create_guest("gw_test")
    assert not await sessions.update_ack(session.id, 2, 0)
    replay = ReplayManager(redis, settings)
    frame = EventFrame(v=1, id="event-1", seq=1, subscription_id="sub-1", name="updated", payload={})
    assert await replay.append(session.id, 1, frame) == 1
    assert not await sessions.update_ack(session.id, 1, 2)
    assert await replay.acknowledge(session.id, 1, 1)
    assert await replay.get_after(session.id, 0) == []


async def test_resume_requires_resync_when_replay_is_missing(redis, settings):
    sessions = SessionManager(redis, settings)
    session, token = await sessions.create_guest("gw_test")
    await redis.hset(keys.session(session.id), "last_seq", 3)
    resumed = await ResumeManager(redis, sessions, settings).resume(token, 0, "gw_next", session.id)
    assert resumed.status is ResumeStatus.RESYNC_REQUIRED
