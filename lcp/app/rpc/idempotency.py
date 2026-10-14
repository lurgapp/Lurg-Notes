import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..protocol.errors import LCPError
from ..storage import keys


class IdempotencyStatus(str, Enum):
    NEW = "new"
    PROCESSING = "processing"
    COMPLETED = "completed"


@dataclass(slots=True)
class IdempotencyResult:
    status: IdempotencyStatus
    payload: dict | None = None
    is_error: bool = False
    lease_id: str | None = None


class IdempotencyManager:
    def __init__(self, redis: Redis, ttl_seconds: int = 86_400, processing_seconds: int = 30) -> None:
        self.redis, self.ttl_seconds, self.processing_seconds = redis, ttl_seconds, processing_seconds

    async def acquire(self, user_id: str, method: str, key: str, payload: dict | None = None) -> IdempotencyResult:
        redis_key = keys.idempotency(user_id, method, key)
        fingerprint = hashlib.sha256(json.dumps(payload or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(redis_key)
                    existing = await pipe.get(redis_key)
                    if existing:
                        if existing == "processing":
                            # Bound pre-upgrade abandoned leases as well.
                            ttl = await pipe.ttl(redis_key)
                            if ttl < 0 or ttl > self.processing_seconds:
                                pipe.multi()
                                pipe.expire(redis_key, self.processing_seconds)
                                await pipe.execute()
                            return IdempotencyResult(IdempotencyStatus.PROCESSING)
                        record = json.loads(existing)
                        if record.get("fingerprint") and record["fingerprint"] != fingerprint:
                            raise LCPError("LCP_IDEMPOTENCY_CONFLICT", "Key was used with a different payload")
                        if record.get("status") == "processing":
                            return IdempotencyResult(IdempotencyStatus.PROCESSING)
                        return IdempotencyResult(IdempotencyStatus.COMPLETED, record["payload"], record["is_error"])
                    lease = uuid4().hex
                    pipe.multi()
                    pipe.set(redis_key, json.dumps({
                        "status": "processing", "fingerprint": fingerprint, "lease_id": lease,
                    }), ex=self.processing_seconds)
                    await pipe.execute()
                    return IdempotencyResult(IdempotencyStatus.NEW, lease_id=lease)
                except WatchError:
                    continue

    async def complete(
        self, user_id: str, method: str, key: str, payload: dict, *, is_error: bool = False,
        lease_id: str | None = None,
    ) -> bool:
        redis_key = keys.idempotency(user_id, method, key)
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(redis_key)
                    existing = await pipe.get(redis_key)
                    record = json.loads(existing) if existing and existing != "processing" else {}
                    if lease_id and record.get("lease_id") != lease_id:
                        return False
                    pipe.multi()
                    if is_error and payload.get("retryable"):
                        pipe.delete(redis_key)
                    else:
                        pipe.set(redis_key, json.dumps({
                            "status": "completed", "fingerprint": record.get("fingerprint"),
                            "payload": payload, "is_error": is_error,
                        }), ex=self.ttl_seconds)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def fail(self, user_id: str, method: str, key: str, *, lease_id: str | None = None) -> None:
        redis_key = keys.idempotency(user_id, method, key)
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(redis_key)
                    existing = await pipe.get(redis_key)
                    record = json.loads(existing) if existing and existing != "processing" else {}
                    if lease_id and record.get("lease_id") != lease_id:
                        return
                    pipe.multi()
                    pipe.delete(redis_key)
                    await pipe.execute()
                    return
                except WatchError:
                    continue
