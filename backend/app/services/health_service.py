import asyncio
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.health import check_database
from app.observability.logging import get_logger
from app.redis import check_redis
from app.schemas.health import CheckStatus, ReadinessStatus

logger = get_logger("app.health")

CHECK_TIMEOUT_SECONDS = 2.0


class HealthService:
    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self._checks: dict[str, Callable[[], Awaitable[None]]] = {
            "database": lambda: check_database(engine),
            "redis": lambda: check_redis(redis),
        }

    async def readiness(self) -> ReadinessStatus:
        names = list(self._checks)
        outcomes = await asyncio.gather(*(self._run(name) for name in names))
        checks: dict[str, CheckStatus] = dict(zip(names, outcomes, strict=True))
        ready = all(outcome == "ok" for outcome in outcomes)
        return ReadinessStatus(status="ready" if ready else "unavailable", checks=checks)

    async def _run(self, name: str) -> CheckStatus:
        try:
            await asyncio.wait_for(self._checks[name](), timeout=CHECK_TIMEOUT_SECONDS)
        except Exception:
            logger.warning("readiness check failed", extra={"check": name}, exc_info=True)
            return "unavailable"
        return "ok"
