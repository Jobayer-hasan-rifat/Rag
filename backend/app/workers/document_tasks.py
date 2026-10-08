import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

from celery import Task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.chunking.chunker import StructureAwareChunker
from app.core.chunking.types import ChunkingConfig
from app.db.repositories.chunk_repository import ChunkRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.section_repository import SectionRepository
from app.db.session import create_engine
from app.observability.context import request_id_var
from app.observability.logging import get_logger
from app.services.processing_service import ProcessingOutcome, ProcessingService
from app.storage.factory import create_storage
from app.workers.dispatch import PROCESS_DOCUMENT_TASK

logger = get_logger("app.workers.documents")


def chunking_config(settings: Settings) -> ChunkingConfig:
    return ChunkingConfig(
        max_size=settings.chunking_max_chars,
        overlap=settings.chunking_overlap_chars,
        min_size=settings.chunking_min_chars,
        max_chunks=settings.chunking_max_chunks,
    )


@asynccontextmanager
async def _service(settings: Settings) -> Any:
    """A ProcessingService with its own short-lived engine (safe across forked workers)."""
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield _build(session, settings)
    finally:
        await engine.dispose()


def _build(session: AsyncSession, settings: Settings) -> ProcessingService:
    return ProcessingService(
        session=session,
        documents=DocumentRepository(session),
        sections=SectionRepository(session),
        chunks=ChunkRepository(session),
        chunker=StructureAwareChunker(chunking_config(settings)),
        storage=create_storage(settings),
        settings=settings,
    )


async def _process(settings: Settings, document_id: uuid.UUID, task_id: str) -> ProcessingOutcome:
    async with _service(settings) as service:
        return await service.process(document_id, task_id=task_id)  # type: ignore[no-any-return]


async def _rechunk(settings: Settings, document_id: uuid.UUID, task_id: str) -> ProcessingOutcome:
    async with _service(settings) as service:
        return await service.rechunk(document_id, task_id=task_id)  # type: ignore[no-any-return]


async def _fail_timed_out(settings: Settings, document_id: uuid.UUID, task_id: str) -> None:
    async with _service(settings) as service:
        await service.fail_timed_out(document_id, task_id=task_id)


@shared_task(bind=True, name=PROCESS_DOCUMENT_TASK, acks_late=True)  # type: ignore[untyped-decorator]
def process_document(self: Task, document_id: str) -> dict[str, str | None]:
    """Extract, normalise and persist one document's text.

    Idempotent: only a `pending` (or abandoned) document can be claimed, results replace
    earlier sections, and retries are bounded by the attempt counter stored on the document.
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
            outcome = asyncio.run(_process(settings, parsed, task_id))
        except SoftTimeLimitExceeded:
            asyncio.run(_fail_timed_out(settings, parsed, task_id))
            return {"status": "failed", "reason": "timeout"}
        if outcome.status == "retry" and outcome.retry_in is not None:
            raise self.retry(countdown=outcome.retry_in)
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


@shared_task(bind=True, name="app.workers.document_tasks.recover_stalled_documents")  # type: ignore[untyped-decorator]
def recover_stalled_documents(self: Task) -> dict[str, int]:
    """Periodic sweep (Celery beat): frees documents stuck in `parsing` and re-queues lost work."""
    settings: Settings = self.app.conf.rag_settings

    def enqueue(document_id: uuid.UUID) -> None:
        self.app.send_task(PROCESS_DOCUMENT_TASK, args=[str(document_id)], ignore_result=True)

    return asyncio.run(_recover(settings, enqueue))


@shared_task(bind=True, name="app.workers.document_tasks.rechunk_document")  # type: ignore[untyped-decorator]
def rechunk_document(self: Task, document_id: str) -> dict[str, str | None]:
    """Regenerate a `chunked` document's chunks from its stored sections.

    Operator/maintenance task (for example after a chunking-version or configuration change):
    Run: celery -A app.workers.celery_app:celery_app call
    app.workers.document_tasks.rechunk_document --args='["<document id>"]'
    """
    settings: Settings = self.app.conf.rag_settings
    task_id = str(self.request.id)
    token = request_id_var.set(f"task-{task_id}")
    try:
        try:
            parsed = uuid.UUID(document_id)
        except ValueError:
            return {"status": "skipped", "reason": "invalid_id"}
        try:
            outcome = asyncio.run(_rechunk(settings, parsed, task_id))
        except SoftTimeLimitExceeded:
            asyncio.run(_fail_timed_out(settings, parsed, task_id))
            return {"status": "failed", "reason": "timeout"}
        return {"status": outcome.status, "reason": outcome.reason}
    finally:
        request_id_var.reset(token)
