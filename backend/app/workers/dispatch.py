import asyncio
import uuid

from celery import Celery

from app.observability.logging import get_logger

logger = get_logger("app.workers.dispatch")

PROCESS_DOCUMENT_TASK = "app.workers.document_tasks.process_document"


class CeleryProcessingQueue:
    """Publishes document-processing tasks from the API process (no task code is imported)."""

    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    def enqueue_sync(self, document_id: uuid.UUID) -> None:
        # Results are never read, so do not subscribe this process to a result channel.
        self._celery.send_task(PROCESS_DOCUMENT_TASK, args=[str(document_id)], ignore_result=True)

    async def enqueue(self, document_id: uuid.UUID) -> bool:
        try:
            await asyncio.to_thread(self.enqueue_sync, document_id)
        except Exception:
            logger.warning(
                "could not queue document processing; the recovery sweep will retry",
                extra={"document_id": str(document_id)},
                exc_info=True,
            )
            return False
        return True
