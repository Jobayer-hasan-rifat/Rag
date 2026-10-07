import hashlib

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.exceptions import RateLimitExceededError
from app.observability.logging import get_logger

logger = get_logger("app.security.rate_limit")


class RateLimiter:
    """Fixed-window counter in Redis. Fails open (and logs) if Redis is unavailable."""

    def __init__(
        self, redis: Redis, *, enabled: bool, max_attempts: int, window_seconds: int
    ) -> None:
        self._redis = redis
        self._enabled = enabled
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds

    async def check(
        self,
        scope: str,
        identifier: str,
        *,
        budget_multiplier: int = 1,
        max_attempts: int | None = None,
    ) -> None:
        """Count one attempt; raise RateLimitExceededError once the window's budget is spent."""
        if not self._enabled:
            return
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
        key = f"ratelimit:{scope}:{digest}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, self._window_seconds, nx=True)
                pipe.ttl(key)
                attempts, _, ttl = await pipe.execute()
        except RedisError:
            logger.error("rate limiter unavailable; allowing request", extra={"scope": scope})
            return
        if attempts > (max_attempts or self._max_attempts * budget_multiplier):
            raise RateLimitExceededError(max(int(ttl), 1))
