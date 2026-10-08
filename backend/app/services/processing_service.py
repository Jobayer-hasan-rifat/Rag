import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.documents.normalization import normalize_text
from app.core.documents.processing import (
    PROCESSING_VERSION,
    SAFE_MESSAGES,
    Deadline,
    ExtractionLimits,
    FailureReason,
    ProcessingFailure,
)
from app.core.documents.scripts import profile_scripts
from app.db.repositories.document_repository import ClaimedDocument, DocumentRepository
from app.db.repositories.section_repository import NewSection, SectionRepository
from app.observability.logging import get_logger
from app.parsers.base import ExtractionResult
from app.parsers.registry import get_extractor
from app.storage.base import ObjectNotFoundError, StorageError, StorageProvider

logger = get_logger("app.processing")

MAX_HEADING_CHARS = 500
MAX_PROPERTY_CHARS = 300
MAX_BACKOFF_SECONDS = 900

OutcomeStatus = Literal["ready", "failed", "retry", "skipped"]


@dataclass(frozen=True)
class ProcessingOutcome:
    status: OutcomeStatus
    reason: str | None = None
    retry_in: int | None = None


@dataclass(frozen=True)
class RecoveryReport:
    released: int
    abandoned: int
    requeued: int


class ProcessingService:
    """Runs the extraction pipeline for one document (worker side)."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        documents: DocumentRepository,
        sections: SectionRepository,
        storage: StorageProvider,
        settings: Settings,
    ) -> None:
        self._session = session
        self._documents = documents
        self._sections = sections
        self._storage = storage
        self._settings = settings
        self._limits = ExtractionLimits(
            max_pages=settings.processing_max_pages,
            max_text_chars=settings.processing_max_text_chars,
            max_docx_uncompressed_bytes=settings.processing_max_docx_uncompressed_bytes,
        )

    async def process(self, document_id: uuid.UUID, *, task_id: str = "-") -> ProcessingOutcome:
        stale_before = datetime.now(UTC) - timedelta(
            seconds=self._settings.processing_stale_after_seconds
        )
        claimed = await self._documents.claim_for_processing(document_id, stale_before=stale_before)
        await self._session.commit()
        if claimed is None:
            logger.info(
                "processing skipped",
                extra={
                    "document_id": str(document_id),
                    "task_id": task_id,
                    "reason": "not_claimable",
                },
            )
            return ProcessingOutcome("skipped", "not_claimable")

        context = {"document_id": str(claimed.id), "task_id": task_id, "attempt": claimed.attempts}
        logger.info("processing started", extra={**context, "file_type": claimed.file_type})
        if claimed.attempts > self._settings.processing_max_attempts:
            return await self._finish_failed(
                claimed, ProcessingFailure(FailureReason.RETRIES_EXHAUSTED), context
            )
        started = time.monotonic()
        try:
            return await self._run(claimed, started, context)
        except ProcessingFailure as failure:
            await self._session.rollback()
            return await self._handle_failure(claimed, failure, context)
        except SQLAlchemyError as error:
            await self._session.rollback()
            db_failure = ProcessingFailure(
                FailureReason.DATABASE_ERROR, retryable=True, detail=type(error).__name__
            )
            return await self._handle_failure(claimed, db_failure, context)
        except Exception as error:
            await self._session.rollback()
            unexpected = ProcessingFailure(
                FailureReason.EXTRACTION_FAILED, detail=type(error).__name__
            )
            logger.exception("unexpected processing error", extra=context)
            return await self._handle_failure(claimed, unexpected, context)

    async def fail_timed_out(self, document_id: uuid.UUID, *, task_id: str = "-") -> None:
        """Called when the worker's hard/soft time limit interrupted processing."""
        await self._session.rollback()
        reason = FailureReason.TIMEOUT
        await self._documents.fail_processing(
            document_id, reason=reason.value, message=SAFE_MESSAGES[reason]
        )
        await self._session.commit()
        logger.warning(
            "processing timed out",
            extra={
                "document_id": str(document_id),
                "task_id": task_id,
                "failure_reason": reason.value,
            },
        )

    async def _run(
        self, claimed: ClaimedDocument, started: float, context: dict[str, Any]
    ) -> ProcessingOutcome:
        data = await self._read(claimed)
        extractor = get_extractor(claimed.file_type)
        deadline = Deadline(self._settings.processing_timeout_seconds)
        extraction = extractor.extract(data, limits=self._limits, deadline=deadline)
        del data
        sections = self._normalise(extraction, extractor.collapse_inline_spaces, deadline)
        character_count = sum(len(section.text) for section in sections)
        has_content = character_count > 0 or any(section.heading for section in sections)
        if not has_content:
            raise ProcessingFailure(FailureReason.EMPTY_DOCUMENT)

        profile = profile_scripts(
            part for section in sections for part in (section.heading or "", section.text)
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        metadata: dict[str, Any] = {
            "processing_version": PROCESSING_VERSION,
            "extractor": extraction.extractor,
            "duration_ms": duration_ms,
            "section_count": len(sections),
            **profile.as_dict(),
        }
        properties = _clean_properties(extraction.properties)
        if properties:
            metadata["properties"] = properties

        await self._sections.replace_all(claimed.id, sections)
        saved = await self._documents.complete_processing(
            claimed.id,
            page_count=extraction.page_count,
            character_count=character_count,
            metadata=metadata,
        )
        if not saved:
            await self._session.rollback()
            logger.info(
                "processing result discarded", extra={**context, "reason": "document_changed"}
            )
            return ProcessingOutcome("skipped", "document_changed")
        await self._session.commit()
        logger.info(
            "processing completed",
            extra={
                **context,
                "extractor": extraction.extractor,
                "duration_ms": duration_ms,
                "page_count": extraction.page_count,
                "character_count": character_count,
                "primary_script": profile.primary,
            },
        )
        return ProcessingOutcome("ready")

    async def _read(self, claimed: ClaimedDocument) -> bytes:
        try:
            await self._storage.size(claimed.storage_key)
            buffer = bytearray()
            async for chunk in self._storage.open(claimed.storage_key):
                buffer.extend(chunk)
                if len(buffer) > claimed.file_size:
                    break
        except ObjectNotFoundError:
            raise ProcessingFailure(FailureReason.STORAGE_MISSING) from None
        except StorageError:
            raise ProcessingFailure(FailureReason.STORAGE_UNAVAILABLE, retryable=True) from None
        if len(buffer) != claimed.file_size:
            raise ProcessingFailure(FailureReason.CORRUPT_DOCUMENT, detail="size mismatch")
        return bytes(buffer)

    @staticmethod
    def _normalise(
        extraction: ExtractionResult, collapse_inline_spaces: bool, deadline: Deadline
    ) -> list[NewSection]:
        sections: list[NewSection] = []
        for section in extraction.sections:
            deadline.check()
            heading = None
            if section.heading is not None:
                heading = normalize_text(section.heading)[:MAX_HEADING_CHARS] or None
            sections.append(
                NewSection(
                    kind=section.kind.value,
                    text=normalize_text(
                        section.text, collapse_inline_spaces=collapse_inline_spaces
                    ),
                    page_number=section.page_number,
                    heading=heading,
                    heading_level=section.heading_level if heading else None,
                )
            )
        return sections

    async def _handle_failure(
        self, claimed: ClaimedDocument, failure: ProcessingFailure, context: dict[str, Any]
    ) -> ProcessingOutcome:
        if failure.retryable and claimed.attempts < self._settings.processing_max_attempts:
            released = await self._documents.release_for_retry(claimed.id)
            await self._session.commit()
            delay = self._backoff(claimed.attempts)
            logger.warning(
                "processing will be retried",
                extra={**context, "failure_reason": failure.reason.value, "retry_in": delay},
            )
            return ProcessingOutcome(
                "retry" if released else "skipped", failure.reason.value, delay
            )
        return await self._finish_failed(claimed, failure, context)

    async def _finish_failed(
        self, claimed: ClaimedDocument, failure: ProcessingFailure, context: dict[str, Any]
    ) -> ProcessingOutcome:
        reason = failure.reason
        await self._documents.fail_processing(
            claimed.id, reason=reason.value, message=SAFE_MESSAGES[reason]
        )
        await self._session.commit()
        logger.warning(
            "processing failed",
            extra={**context, "failure_reason": reason.value, "detail": failure.detail},
        )
        return ProcessingOutcome("failed", reason.value)

    def _backoff(self, attempts: int) -> int:
        base = self._settings.processing_retry_backoff_seconds
        return int(min(base * 2 ** max(attempts - 1, 0), MAX_BACKOFF_SECONDS))

    async def recover(self, enqueue: Callable[[uuid.UUID], None]) -> RecoveryReport:
        """Release abandoned `parsing` documents and re-queue ones whose message was lost."""
        settings = self._settings
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=settings.processing_stale_after_seconds)
        batch = settings.processing_sweep_batch_size
        released = abandoned = 0
        to_queue: list[uuid.UUID] = []

        for document_id, attempts in await self._documents.find_stalled(stale_before, batch):
            if attempts >= settings.processing_max_attempts:
                reason = FailureReason.RETRIES_EXHAUSTED
                if await self._documents.fail_processing(
                    document_id,
                    reason=reason.value,
                    message=SAFE_MESSAGES[reason],
                    started_before=stale_before,
                ):
                    abandoned += 1
            elif await self._documents.release_for_retry(document_id, started_before=stale_before):
                released += 1
                to_queue.append(document_id)

        idle_since = now - timedelta(seconds=settings.processing_pending_requeue_after_seconds)
        waiting = await self._documents.find_waiting(idle_since, batch)
        to_queue.extend(i for i in waiting if i not in to_queue)
        await self._documents.touch(to_queue)
        await self._session.commit()

        requeued = 0
        for document_id in to_queue:
            try:
                enqueue(document_id)
                requeued += 1
            except Exception:
                logger.warning(
                    "could not re-queue document", extra={"document_id": str(document_id)}
                )
        if released or abandoned or requeued:
            logger.warning(
                "recovery sweep acted",
                extra={"released": released, "abandoned": abandoned, "requeued": requeued},
            )
        return RecoveryReport(released=released, abandoned=abandoned, requeued=requeued)


def _clean_properties(properties: dict[str, Any]) -> dict[str, str]:
    """File-embedded metadata is untrusted: normalise it and bound its size."""
    cleaned: dict[str, str] = {}
    for key, value in properties.items():
        if isinstance(value, (str, int)):
            text = normalize_text(str(value))[:MAX_PROPERTY_CHARS]
            if text:
                cleaned[key] = text
    return cleaned
