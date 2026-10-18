import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import Task, shared_task
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repositories.refresh_token_repository import RefreshTokenRepository
from app.db.session import create_engine
from app.observability.logging import get_logger

logger = get_logger("app.workers.maintenance")

PURGE_REFRESH_TOKENS_TASK = "app.workers.maintenance_tasks.purge_refresh_tokens"
EXPIRED_GRACE = timedelta(days=1)


async def _purge(settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            deleted = await RefreshTokenRepository(session).delete_expired(
                datetime.now(UTC) - EXPIRED_GRACE
            )
            await session.commit()
            return deleted
    finally:
        await engine.dispose()


@shared_task(bind=True, name=PURGE_REFRESH_TOKENS_TASK)  # type: ignore[untyped-decorator]
def purge_refresh_tokens(self: Task) -> dict[str, Any]:
    """Periodic sweep (Celery beat): deletes refresh-token rows that expired a day or more ago."""
    settings: Settings = self.app.conf.rag_settings
    deleted = asyncio.run(_purge(settings))
    logger.info("expired refresh tokens removed", extra={"count": deleted})
    return {"deleted": deleted}
