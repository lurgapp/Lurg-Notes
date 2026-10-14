"""Publish minimal public Nearby changes to the durable LCP event stream after commit."""

import json

from redis.asyncio import Redis

from app.core.config import settings
from app.models.entities import Event


async def publish_event(event: Event, name: str) -> None:
    if not settings.lcp_event_bus_redis_url:
        return
    redis = Redis.from_url(
        settings.lcp_event_bus_redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
    )
    try:
        payload = {
            "name": name,
            "event_id": str(event.id),
            "category": event.category.value,
            "severity": event.severity,
            "latitude": event.latitude,
            "longitude": event.longitude,
        }
        # Publish exactly once to the stream, never also to legacy Pub/Sub.
        # LCP's consumer group persists fanout before ACK and deduplicates retries by stream id.
        await redis.xadd(
            "lcp:v1:event-stream",
            {"topic": "nearby.events", "payload": json.dumps(payload, separators=(",", ":"))},
            maxlen=100_000,
            approximate=True,
        )
    finally:
        await redis.aclose()
