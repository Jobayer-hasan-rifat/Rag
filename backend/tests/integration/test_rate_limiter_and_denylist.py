import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from app.exceptions import RateLimitExceededError
from app.security.rate_limit import RateLimiter
from app.security.token_denylist import TokenDenylist

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(f"{redis_url}/5", decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def _limiter(
    redis: Redis, *, attempts: int = 3, window: int = 60, enabled: bool = True
) -> RateLimiter:
    return RateLimiter(redis, enabled=enabled, max_attempts=attempts, window_seconds=window)


async def test_attempts_within_budget_are_allowed(redis: Redis) -> None:
    limiter = _limiter(redis, attempts=3)

    for _ in range(3):
        await limiter.check("login", "1.2.3.4")


async def test_attempt_over_budget_is_rejected_with_retry_after(redis: Redis) -> None:
    limiter = _limiter(redis, attempts=2)
    await limiter.check("login", "1.2.3.4")
    await limiter.check("login", "1.2.3.4")

    with pytest.raises(RateLimitExceededError) as error:
        await limiter.check("login", "1.2.3.4")

    assert error.value.status_code == 429
    assert 1 <= int(error.value.headers["Retry-After"]) <= 60


async def test_budgets_are_independent_per_scope_and_identifier(redis: Redis) -> None:
    limiter = _limiter(redis, attempts=1)
    await limiter.check("login", "a")

    await limiter.check("login", "b")
    await limiter.check("register", "a")
    with pytest.raises(RateLimitExceededError):
        await limiter.check("login", "a")


async def test_budget_resets_after_the_window(redis: Redis) -> None:
    limiter = _limiter(redis, attempts=1, window=1)
    await limiter.check("login", "a")
    with pytest.raises(RateLimitExceededError):
        await limiter.check("login", "a")

    await asyncio.sleep(1.2)

    await limiter.check("login", "a")


async def test_disabled_limiter_never_blocks(redis: Redis) -> None:
    limiter = _limiter(redis, attempts=1, enabled=False)

    for _ in range(5):
        await limiter.check("login", "a")


async def test_identifiers_are_not_stored_in_clear_text(redis: Redis) -> None:
    await _limiter(redis).check("login", "victim@example.com")

    keys = [key async for key in redis.scan_iter("ratelimit:*")]

    assert keys
    assert all("victim" not in key for key in keys)


async def test_limiter_fails_open_when_redis_is_unreachable() -> None:
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        await _limiter(dead, attempts=1).check("login", "a")
        await _limiter(dead, attempts=1).check("login", "a")
    finally:
        await dead.aclose()


async def test_denylist_marks_token_revoked_until_expiry(redis: Redis) -> None:
    denylist = TokenDenylist(redis)

    assert not await denylist.is_revoked("jti-1")
    await denylist.revoke("jti-1", datetime.now(UTC) + timedelta(minutes=5))

    assert await denylist.is_revoked("jti-1")
    assert not await denylist.is_revoked("jti-2")
    assert 0 < await redis.ttl("auth:denylist:jti-1") <= 300


async def test_denylist_ignores_tokens_that_have_already_expired(redis: Redis) -> None:
    denylist = TokenDenylist(redis)

    await denylist.revoke("jti-old", datetime.now(UTC) - timedelta(seconds=5))

    assert not await denylist.is_revoked("jti-old")


async def test_budget_multiplier_enlarges_the_allowance(redis: Redis) -> None:
    limiter = _limiter(redis, attempts=2)

    for _ in range(6):
        await limiter.check("login:email", "a", budget_multiplier=3)
    with pytest.raises(RateLimitExceededError):
        await limiter.check("login:email", "a", budget_multiplier=3)
