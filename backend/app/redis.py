from redis.asyncio import Redis

from app.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    client: Redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )
    return client


async def check_redis(client: Redis) -> None:
    if not await client.ping():
        raise ConnectionError("Redis did not answer PING")
