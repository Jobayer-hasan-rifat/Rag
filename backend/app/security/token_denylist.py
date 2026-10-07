from datetime import UTC, datetime

from redis.asyncio import Redis

_KEY_PREFIX = "auth:denylist:"


class TokenDenylist:
    """Revoked access-token IDs. Entries live only until the token would have expired anyway."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        ttl = int((expires_at - datetime.now(UTC)).total_seconds())
        if ttl > 0:
            await self._redis.set(f"{_KEY_PREFIX}{jti}", "1", ex=ttl)

    async def is_revoked(self, jti: str) -> bool:
        return bool(await self._redis.exists(f"{_KEY_PREFIX}{jti}"))
