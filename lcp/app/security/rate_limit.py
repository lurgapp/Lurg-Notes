import time
from dataclasses import dataclass

from redis.asyncio import Redis

from ..storage import keys


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_ms: int | None = None


class RateLimiter:
    """Atomic fixed window limiter; method-specific limits can be added without pipeline changes."""

    def __init__(self, redis: Redis, limit: int = 120, window_seconds: int = 60) -> None:
        self.redis, self.limit, self.window_seconds = redis, limit, window_seconds

    async def check(self, principal_key: str, method: str) -> RateLimitResult:
        window = int(time.time() // self.window_seconds)
        key = keys.rate_limit(principal_key, method, window)
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, self.window_seconds + 1)
        if count <= self.limit:
            return RateLimitResult(True)
        ttl = await self.redis.ttl(key)
        return RateLimitResult(False, max(1, ttl) * 1000)
