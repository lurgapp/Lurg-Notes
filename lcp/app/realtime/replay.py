import time

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..config import Settings
from ..protocol.models import EventFrame
from ..storage import keys


class ReplayManager:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis, self.settings = redis, settings

    async def append(
        self, session_id: str, generation: int | None, frame: EventFrame, *, event_key: str | None = None
    ) -> int:
        session_key, replay_key = keys.session(session_id), keys.replay(session_id)
        age_key, seen_key = f"{replay_key}:age", keys.replay_seen(session_id)
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(session_key, replay_key, age_key, seen_key)
                    session = await pipe.hgetall(session_key)
                    if not session or int(session["expires_at"]) <= int(time.time()):
                        raise RuntimeError("expired session")
                    if generation is not None and int(session["generation"]) != generation:
                        raise RuntimeError("stale session generation")
                    if event_key:
                        existing = await pipe.zscore(seen_key, event_key)
                        if existing:
                            frame.seq = int(existing)
                            return frame.seq
                    seq = int(session.get("last_seq", 0)) + 1
                    frame.seq = seq
                    serialized = frame.model_dump_json(exclude_none=True)
                    expired = await pipe.zrangebyscore(
                        age_key, "-inf", time.time() - self.settings.lcp_replay_max_age_seconds
                    )
                    count = await pipe.zcard(replay_key)
                    overflow = max(0, count + 1 - self.settings.lcp_replay_max_events)
                    oldest = await pipe.zrange(replay_key, 0, overflow - 1) if overflow else []
                    removed = list(set(expired + oldest))
                    ttl = min(
                        self.settings.lcp_replay_max_age_seconds,
                        max(1, int(session["expires_at"]) - int(time.time())),
                    )
                    pipe.multi()
                    pipe.hset(session_key, "last_seq", seq)
                    if removed:
                        pipe.zrem(replay_key, *removed)
                        pipe.zrem(age_key, *removed)
                    pipe.zadd(replay_key, {serialized: seq})
                    pipe.zadd(age_key, {serialized: time.time()})
                    if event_key:
                        pipe.zadd(seen_key, {event_key: seq})
                        pipe.zremrangebyrank(seen_key, 0, -self.settings.lcp_replay_max_events - 1)
                    pipe.expire(replay_key, ttl)
                    pipe.expire(age_key, ttl)
                    pipe.expire(seen_key, ttl)
                    await pipe.execute()
                    return seq
                except WatchError:
                    continue

    async def trim_expired(self, session_id: str) -> None:
        replay_key = keys.replay(session_id)
        expired = await self.redis.zrangebyscore(
            f"{replay_key}:age", "-inf", time.time() - self.settings.lcp_replay_max_age_seconds
        )
        if expired:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zrem(replay_key, *expired)
                pipe.zrem(f"{replay_key}:age", *expired)
                await pipe.execute()

    async def get_after(self, session_id: str, seq: int, *, count: int | None = None) -> list[EventFrame]:
        await self.trim_expired(session_id)
        kwargs = {"start": 0, "num": count} if count is not None else {}
        values = await self.redis.zrangebyscore(keys.replay(session_id), f"({seq}", "+inf", **kwargs)
        return [EventFrame.model_validate_json(value) for value in values]

    async def acknowledge(self, session_id: str, generation: int, seq: int) -> bool:
        session_key, replay_key = keys.session(session_id), keys.replay(session_id)
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(session_key, replay_key)
                    data = await pipe.hgetall(session_key)
                    if not data or int(data["generation"]) != generation or seq > int(data.get("last_seq", 0)):
                        return False
                    if int(data["expires_at"]) <= int(time.time()):
                        return False
                    acknowledged = await pipe.zrangebyscore(replay_key, "-inf", seq)
                    pipe.multi()
                    pipe.hset(session_key, "last_acked_seq", max(seq, int(data.get("last_acked_seq", 0))))
                    pipe.zremrangebyscore(replay_key, "-inf", seq)
                    if acknowledged:
                        pipe.zrem(f"{replay_key}:age", *acknowledged)
                    # Keep dedup records until retention expiry: a pending stream item can be redelivered after ACK.
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def can_replay_from(self, session_id: str, seq: int) -> bool:
        await self.trim_expired(session_id)
        last_seq = await self.redis.hget(keys.session(session_id), "last_seq")
        if last_seq is None or seq >= int(last_seq):
            return last_seq is not None and seq == int(last_seq)
        first = await self.redis.zrange(keys.replay(session_id), 0, 0, withscores=True)
        return bool(first) and seq >= int(first[0][1]) - 1
