import json
import time
from dataclasses import asdict, dataclass
from typing import ClassVar
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..connection.context import ConnectionContext
from ..storage import keys


@dataclass(slots=True)
class Subscription:
    id: str
    session_id: str
    topic: str
    filter: dict
    owner_id: str | None = None


class SubscriptionManager:
    PRIVATE_TOPICS: ClassVar[set[str]] = {"graph.events", "notes.events"}
    ALLOWED_TOPICS: ClassVar[set[str]] = {"nearby.events", *PRIVATE_TOPICS}

    def __init__(self, redis: Redis, max_subscriptions: int = 32) -> None:
        self.redis, self.max_subscriptions = redis, max_subscriptions

    async def subscribe(self, context: ConnectionContext, topic: str, filter: dict) -> Subscription:
        if not context.session_id or context.generation is None:
            raise ValueError("A session is required")
        if topic not in self.ALLOWED_TOPICS:
            raise ValueError("Unknown topic")
        session_key, sub_key = keys.session(context.session_id), keys.subscriptions(context.session_id)
        if topic in self.PRIVATE_TOPICS and any(key in filter for key in ("owner_id", "user_id")):
            raise ValueError("Owner filters are server-controlled")
        subscription = Subscription(f"sub_{uuid4().hex}", context.session_id, topic, filter)
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(session_key, sub_key)
                    data = await pipe.hgetall(session_key)
                    if not data or int(data["generation"]) != context.generation:
                        raise ValueError("Session ownership changed")
                    ttl = int(data["expires_at"]) - int(time.time())
                    if ttl <= 0:
                        raise ValueError("Session expired")
                    if topic in self.PRIVATE_TOPICS and not data.get("user_id"):
                        raise ValueError("An account session is required for this topic")
                    subscription.owner_id = data.get("user_id") or None
                    value = json.dumps(asdict(subscription), separators=(",", ":"))
                    if await pipe.hlen(sub_key) >= self.max_subscriptions:
                        raise ValueError("Subscription limit reached")
                    pipe.multi()
                    pipe.hset(sub_key, subscription.id, value)
                    pipe.expire(sub_key, ttl)
                    pipe.hset(keys.topic_subscriptions(topic), subscription.id, value)
                    # A topic contains sessions with different expiry times; session TTL owns each member.
                    pipe.persist(keys.topic_subscriptions(topic))
                    await pipe.execute()
                    context.subscriptions[subscription.id] = subscription
                    return subscription
                except WatchError:
                    continue

    async def unsubscribe(self, context: ConnectionContext, subscription_id: str) -> None:
        if not context.session_id:
            return
        value = await self.redis.hget(keys.subscriptions(context.session_id), subscription_id)
        if value:
            topic = json.loads(value)["topic"]
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.hdel(keys.subscriptions(context.session_id), subscription_id)
                pipe.hdel(keys.topic_subscriptions(topic), subscription_id)
                await pipe.execute()
        context.subscriptions.pop(subscription_id, None)

    async def restore(self, session_id: str) -> list[Subscription]:
        values = await self.redis.hgetall(keys.subscriptions(session_id))
        subscriptions = [Subscription(**json.loads(value)) for value in values.values()]
        # Existing v0.1 sessions predate the global offline fanout index.
        ttl = await self.redis.ttl(keys.session(session_id))
        if subscriptions and ttl > 0:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.expire(keys.subscriptions(session_id), ttl)
                for sub in subscriptions:
                    pipe.hset(keys.topic_subscriptions(sub.topic), sub.id, json.dumps(asdict(sub)))
                    pipe.persist(keys.topic_subscriptions(sub.topic))
                await pipe.execute()
        return subscriptions

    async def for_topic(self, topic: str):
        # HSCAN bounds a fanout batch; expired sessions lazily clean the index.
        async for sub_id, value in self.redis.hscan_iter(keys.topic_subscriptions(topic), count=128):
            subscription = Subscription(**json.loads(value))
            data = await self.redis.hgetall(keys.session(subscription.session_id))
            current = await self.redis.hget(keys.subscriptions(subscription.session_id), sub_id)
            if not data or not current or int(data["expires_at"]) <= int(time.time()):
                await self.redis.hdel(keys.topic_subscriptions(topic), sub_id)
                continue
            yield subscription

    async def migrate_existing(self) -> None:
        async for key in self.redis.scan_iter(match=f"{keys.SUBSCRIPTIONS_PREFIX}*", count=128):
            await self.restore(key.removeprefix(keys.SUBSCRIPTIONS_PREFIX))
