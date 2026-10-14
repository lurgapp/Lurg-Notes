import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError, WatchError

from ..config import Settings
from ..storage import keys

EventHandler = Callable[[str, dict, str, str | None], Awaitable[None]]


class EventBus:
    async def publish(
        self, topic: str, payload: dict, *, owner_id: str | None = None, source_event_id: str | None = None
    ) -> None: ...
    async def subscribe(self, topic: str, handler: EventHandler) -> None: ...

    async def subscribe_many(self, topics: list[str], handler: EventHandler) -> None:
        for topic in topics:
            await self.subscribe(topic, handler)


class RedisEventBus(EventBus):
    """Durable Redis Streams fanout, with a transitional legacy Pub/Sub ingress bridge."""

    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self.redis, self.settings = redis, settings or Settings()
        self._pubsub, self._task, self._bridge_task = None, None, None
        self._handlers: dict[str, list[EventHandler]] = {}
        self._stopping = asyncio.Event()
        self.consumer = f"{self.settings.lcp_gateway_id}:{uuid4().hex}"
        self.healthy, self.bridge_healthy = False, not self.settings.lcp_legacy_pubsub_enabled
        self.log = logging.getLogger("lcp")
        self._failed_events: set[str] = set()

    @property
    def is_ready(self) -> bool:
        return bool(
            self.healthy and self.bridge_healthy and self._task and not self._task.done()
            and (not self.settings.lcp_legacy_pubsub_enabled or (
                self._bridge_task and not self._bridge_task.done()
            ))
        )

    @staticmethod
    def channel(topic: str) -> str:
        return f"lcp:v1:events:{topic}"

    async def publish(
        self, topic: str, payload: dict, *, owner_id: str | None = None, source_event_id: str | None = None
    ) -> None:
        fields = {"topic": topic, "payload": json.dumps(payload, separators=(",", ":"))}
        if owner_id is not None:
            fields["owner_id"] = owner_id
        if source_event_id is not None:
            fields["source_event_id"] = source_event_id
        await self.redis.xadd(
            keys.EVENT_STREAM, fields,
            maxlen=self.settings.lcp_event_stream_max_events, approximate=True,
        )

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        await self.subscribe_many([topic], handler)

    async def subscribe_many(self, topics: list[str], handler: EventHandler) -> None:
        # Install the complete callback set before a consumer can ACK a startup backlog.
        for topic in topics:
            self._handlers.setdefault(topic, []).append(handler)
        try:
            await self.redis.xgroup_create(keys.EVENT_STREAM, keys.EVENT_GROUP, id="0-0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        if self.settings.lcp_legacy_pubsub_enabled:
            if self._pubsub is None:
                self._pubsub = self.redis.pubsub()
            await self._pubsub.subscribe(*(self.channel(topic) for topic in topics))
            if self._bridge_task is None:
                self._bridge_task = asyncio.create_task(self._bridge(), name="lcp-legacy-pubsub-bridge")
        if self._task is None:
            self._task = asyncio.create_task(self._consume(), name="lcp-event-stream")

    async def _process(self, event_id: str, fields: dict) -> None:
        try:
            topic, payload = fields["topic"], json.loads(fields["payload"])
            if not isinstance(payload, dict) or not isinstance(topic, str):
                raise TypeError("Invalid event envelope")
        except (ValueError, KeyError, TypeError):
            self.log.warning("lcp_invalid_event_dropped")
            await self.redis.xack(keys.EVENT_STREAM, keys.EVENT_GROUP, event_id)
            return
        if topic not in self._handlers:
            raise ValueError("No consumer registered for event topic")
        for handler in self._handlers[topic]:
            await handler(topic, payload, fields.get("source_event_id") or event_id, fields.get("owner_id"))
        await self.redis.xack(keys.EVENT_STREAM, keys.EVENT_GROUP, event_id)

    async def _consume(self) -> None:
        while not self._stopping.is_set():
            try:
                # Recover unacknowledged fanout after worker failure. Replay writes deduplicate by stream id.
                claimed = await self.redis.xautoclaim(
                    keys.EVENT_STREAM, keys.EVENT_GROUP, self.consumer, 30_000, "0-0", count=128
                )
                messages = claimed[1]
                if not messages:
                    batches = await self.redis.xreadgroup(
                        keys.EVENT_GROUP, self.consumer, {keys.EVENT_STREAM: ">"}, count=128, block=1000
                    )
                    messages = [item for _, items in batches for item in items]
                self.healthy = not self._failed_events
                await asyncio.sleep(0.01)
                for event_id, fields in messages:
                    try:
                        await self._process(event_id, fields)
                        self._failed_events.discard(event_id)
                        self.healthy = not self._failed_events
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self.healthy = False
                        self._failed_events.add(event_id)
                        self.log.exception("lcp_event_fanout_retry")
                        # Keep this stream item pending; don't kill other consumers or ACK failed fanout.
                        await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.healthy = False
                self.log.exception("lcp_event_stream_reconnect")
                await asyncio.sleep(1)
        self.healthy = False

    async def _owns_legacy_ingress(self) -> bool:
        # Only one gateway converts a legacy publication into a durable stream event.
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(keys.LEGACY_LEADER)
                    owner = await pipe.get(keys.LEGACY_LEADER)
                    if owner and owner != self.consumer:
                        return False
                    pipe.multi()
                    pipe.set(keys.LEGACY_LEADER, self.consumer, ex=5)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def _bridge(self) -> None:
        while not self._stopping.is_set():
            try:
                owner = await self._owns_legacy_ingress()
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                self.bridge_healthy = True
                await asyncio.sleep(0.01)
                if not message or not owner:
                    continue
                topic = str(message["channel"]).removeprefix("lcp:v1:events:")
                try:
                    payload = json.loads(message["data"])
                    if not isinstance(payload, dict):
                        raise TypeError("Invalid legacy event")
                except (json.JSONDecodeError, TypeError, ValueError):
                    self.log.warning("lcp_invalid_legacy_event_dropped")
                    continue
                await self.publish(topic, payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.bridge_healthy = False
                self.log.exception("lcp_legacy_ingress_reconnect")
                await asyncio.sleep(1)
        self.bridge_healthy = False

    async def close(self) -> None:
        self._stopping.set()
        for task in (self._task, self._bridge_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._pubsub:
            await self._pubsub.aclose()
        self.healthy, self.bridge_healthy = False, False
