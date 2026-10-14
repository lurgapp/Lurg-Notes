"""At-least-once product outbox delivery to LCP's private event stream."""
import asyncio
import json
import logging
from uuid import UUID

from sqlalchemy import text
from ...config import settings
from ...db import Session

STREAM = "lcp:v1:event-stream"
logger = logging.getLogger(__name__)
TOPICS = {"user_graph": "graph.events", "user_notes": "notes.events"}


def event_fields(row: dict) -> dict[str, str]:
    # Ownership comes from the trusted aggregate column, never client payload.
    owner = str(UUID(str(row["aggregate_id"])))
    payload = {"name": row["event_type"]}
    if row["aggregate_type"] == "user_notes":
        # Clients fence queued callbacks against their captured account too.
        # This is the authenticated aggregate owner, never a client field.
        payload["owner_id"] = owner
    raw_payload = row.get("payload")
    candidate = raw_payload.get("candidate_id") if isinstance(raw_payload, dict) else None
    if candidate:
        try:
            payload["candidate_id"] = str(UUID(str(candidate)))
        except ValueError:
            pass
    return {"topic": TOPICS[row["aggregate_type"]], "owner_id": owner,
            "source_event_id": str(row["id"]), "payload": json.dumps(payload, separators=(",", ":"))}


async def publish_once(redis, session_factory=Session, batch_size: int = 20) -> int:
    async with session_factory() as db:
        await db.execute(text("SET LOCAL statement_timeout='2s'"))
        rows = (await db.execute(text("""SELECT id,aggregate_type,aggregate_id,event_type,payload FROM outbox_events
          WHERE published_at IS NULL AND ((aggregate_type='user_graph' AND event_type='graph.updated')
            OR (aggregate_type='user_notes' AND event_type IN ('notes.updated','notes.deleted')))
          ORDER BY created_at,id LIMIT :limit FOR UPDATE SKIP LOCKED"""), {"limit": min(max(batch_size, 1), 100)})).mappings().all()
        if not rows:
            return 0
        # A partial Redis success followed by failure is retried with the same
        # source id. LCP's persisted fanout deduplicates it per subscription.
        async with redis.pipeline(transaction=False) as pipeline:
            for row in rows:
                pipeline.xadd(STREAM, event_fields(dict(row)), maxlen=100_000, approximate=True)
            async with asyncio.timeout(2):
                responses = await pipeline.execute()
            if len(responses) != len(rows) or not all(responses):
                raise RuntimeError("Event stream did not acknowledge the complete batch")
        for row in rows:
            await db.execute(text("UPDATE outbox_events SET published_at=now() WHERE id=:id AND published_at IS NULL"), {"id": row["id"]})
        await db.commit()
        return len(rows)


async def run_outbox_publisher(session_factory=Session) -> None:
    if not settings.lcp_event_bus_redis_url:
        logger.info("outbox publisher disabled: event bus is not configured")
        return
    from redis.asyncio import Redis
    redis = Redis.from_url(settings.lcp_event_bus_redis_url, decode_responses=True,
                           socket_connect_timeout=2, socket_timeout=2, max_connections=2)
    try:
        while True:
            try:
                delivered = await publish_once(redis, session_factory)
                # One bounded publisher for the whole product, not per user.
                # A short idle interval keeps foreground Notes invalidations
                # responsive without polling from every connected device.
                await asyncio.sleep(0.1 if delivered else 0.5)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("outbox delivery retry: %s", type(error).__name__)
                await asyncio.sleep(5)
    finally:
        await redis.aclose()
