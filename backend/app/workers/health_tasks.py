from typing import Any

from celery import shared_task

from app.observability.logging import get_logger

logger = get_logger("app.workers.health")


@shared_task(name="app.workers.health_tasks.health_check_task")  # type: ignore[untyped-decorator]
def health_check_task() -> dict[str, Any]:
    logger.info("health check task executed")
    return {"status": "ok"}
