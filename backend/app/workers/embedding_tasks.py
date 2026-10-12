import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

from celery import Task, shared_task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.session import create_engine
from app.embeddings.registry import get_provider
from app.observability.context import request_id_var
from app.observability.logging import get_logger
from app.services.embedding_service import EmbeddingOutcome, EmbeddingService
from app.workers.dispatch import EMBED_DOCUMENT_TASK

logger = get_logger("app.workers.embedding")


@asynccontextmanager
async def _service(settings: Settings) -> Any:
    """An EmbeddingService with its own short-lived engine; the model itself is per process."""
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield EmbeddingService(
                session=session,
                documents=DocumentRepository(session),
                embeddings=EmbeddingRepository(session),
                provider=get_provider(settings),
                settings=settings,
            )
    finally:
        await engine.dispose()


async def _embed(settings: Settings, document_id: uuid.UUID, task_id: str) -> EmbeddingOutcome:
    async with _service(settings) as service:
        return await service.embed(document_id, task_id=task_id)  # type: ignore[no-any-return]


async def _handle_timeout(
    settings: Settings, document_id: uuid.UUID, task_id: str
) -> EmbeddingOutcome:
    async with _service(settings) as service:
        return await service.handle_timeout(document_id, task_id=task_id)  # type: ignore[no-any-return]


@shared_task(bind=True, name=EMBED_DOCUMENT_TASK, acks_late=True)  # type: ignore[untyped-decorator]
def embed_document(self: Task, document_id: str) -> dict[str, str | None]:
    """Embed one chunked document's chunks and make it searchable.

    Idempotent: only a chunked, stale-embedding or re-indexable document can be claimed, vectors
    already stored are kept and skipped, and the time spent per task is bounded (a long document
    is processed over several tasks, each resuming where the last stopped).
    """
    settings: Settings = self.app.conf.rag_settings
    task_id = str(self.request.id)
    token = request_id_var.set(f"task-{task_id}")
    try:
        try:
            parsed = uuid.UUID(document_id)
        except ValueError:
            logger.error("ignoring task with an invalid document id", extra={"task_id": task_id})
            return {"status": "skipped", "reason": "invalid_id"}
        try:
            outcome = asyncio.run(_embed(settings, parsed, task_id))
        except SoftTimeLimitExceeded:
            timed_out = asyncio.run(_handle_timeout(settings, parsed, task_id))
            if timed_out.status == "retry":
                self.app.send_task(
                    EMBED_DOCUMENT_TASK,
                    args=[document_id],
                    countdown=timed_out.retry_in or 0,
                    ignore_result=True,
                )
            return {"status": timed_out.status, "reason": "timeout"}
        if outcome.status == "retry" and outcome.retry_in is not None:
            try:
                raise self.retry(countdown=outcome.retry_in)
            except MaxRetriesExceededError:
                # the document is already released to `chunked`; the sweep queues it again
                return {"status": "retry", "reason": outcome.reason}
        if outcome.status == "continue":
            self.app.send_task(EMBED_DOCUMENT_TASK, args=[document_id], ignore_result=True)
        return {"status": outcome.status, "reason": outcome.reason}
    finally:
        request_id_var.reset(token)


async def _recover(settings: Settings, enqueue: Any) -> dict[str, int]:
    async with _service(settings) as service:
        report = await service.recover(enqueue)
    return {
        "released": report.released,
        "abandoned": report.abandoned,
        "requeued": report.requeued,
    }


@shared_task(bind=True, name="app.workers.embedding_tasks.recover_stalled_embeddings")  # type: ignore[untyped-decorator]
def recover_stalled_embeddings(self: Task) -> dict[str, int]:
    """Periodic sweep (Celery beat): frees stalled `embedding` documents, queues lost work."""
    settings: Settings = self.app.conf.rag_settings

    def enqueue(document_id: uuid.UUID) -> None:
        self.app.send_task(EMBED_DOCUMENT_TASK, args=[str(document_id)], ignore_result=True)

    return asyncio.run(_recover(settings, enqueue))


async def _reindex(settings: Settings, enqueue: Any, limit: int, include_failed: bool) -> int:
    async with _service(settings) as service:
        return await service.reindex(enqueue, limit=limit, include_failed=include_failed)  # type: ignore[no-any-return]


@shared_task(bind=True, name="app.workers.embedding_tasks.reindex_embeddings")  # type: ignore[untyped-decorator]
def reindex_embeddings(self: Task, limit: int = 500, include_failed: bool = False) -> int:
    """Queue documents whose vectors belong to a different embedding configuration.

    Run this after changing the embedding model, revision or input settings:
    celery -A app.workers.celery_app:celery_app call app.workers.embedding_tasks.reindex_embeddings
    Re-indexing is never automatic: until a document is re-embedded it is not searchable under
    the new configuration, so run it deliberately (and expect a window with reduced results).
    """
    settings: Settings = self.app.conf.rag_settings
    limit = max(1, min(int(limit), 5000))

    def enqueue(document_id: uuid.UUID) -> None:
        self.app.send_task(EMBED_DOCUMENT_TASK, args=[str(document_id)], ignore_result=True)

    return asyncio.run(_reindex(settings, enqueue, limit, include_failed))
