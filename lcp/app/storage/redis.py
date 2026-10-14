from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(
        url, decode_responses=True, health_check_interval=20, socket_connect_timeout=2, socket_timeout=5
    )
