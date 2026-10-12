import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.documents.processing import SAFE_MESSAGES, FailureReason
from app.db.repositories.document_repository import ClaimedEmbedding, DocumentRepository
from app.db.repositories.embedding_repository import (
    EmbeddingRepository,
    PendingChunk,
    VectorRow,
)
from app.embeddings.inputs import passage_input
from app.embeddings.types import (
    EmbeddingError,
    EmbeddingProvider,
    InvalidEmbeddingError,
    ModelUnavailableError,
)
from app.observability.logging import get_logger

logger = get_logger("app.embedding")

MAX_BACKOFF_SECONDS = 900

OutcomeStatus = Literal["ready", "continue", "retry", "failed", "skipped"]


@dataclass(frozen=True)
class EmbeddingOutcome:
    status: OutcomeStatus
    reason: str | None = None
    retry_in: int | None = None


@dataclass(frozen=True)
class EmbeddingRecoveryReport:
    released: int
    abandoned: int
    requeued: int


@dataclass
class _Run:
    """Counters for one claim, reported once at the end (never any text)."""

    batches: int = 0
    chunks: int = 0
    truncated: int = 0
    model_seconds: float = 0.0


class _Failure(Exception):
    def __init__(self, reason: FailureReason, *, retryable: bool = False, detail: str = "") -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.retryable = retryable
        self.detail = detail


class EmbeddingService:
    """Worker-side stage after chunking: embed a document's chunks and make it searchable.

    One claim processes chunks in batches, committing after each, until everything is embedded
    or the time budget is spent (the document then goes back to `chunked` and a new task resumes).
    Vectors written so far are kept, so progress is never lost and re-runs only do what is left.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        documents: DocumentRepository,
        embeddings: EmbeddingRepository,
        provider: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self._session = session
        self._documents = documents
        self._embeddings = embeddings
        self._provider = provider
        self._settings = settings

    # --- entry points -------------------------------------------------------------------

    async def embed(self, document_id: uuid.UUID, *, task_id: str = "-") -> EmbeddingOutcome:
        config = self._provider.config
        config_id = await self._embeddings.ensure_config(config)
        await self._session.commit()
        stale_before = datetime.now(UTC) - timedelta(
            seconds=self._settings.embedding_stale_after_seconds
        )
        claimed = await self._documents.claim_for_embedding(
            document_id, config_id=config_id, stale_before=stale_before
        )
        await self._session.commit()
        if claimed is None:
            logger.info(
                "embedding skipped",
                extra={
                    "document_id": str(document_id),
                    "task_id": task_id,
                    "reason": "not_claimable",
                },
            )
            return EmbeddingOutcome("skipped", "not_claimable")
        context = {"document_id": str(claimed.id), "task_id": task_id, "attempt": claimed.attempts}
        logger.info(
            "embedding started",
            extra={
                **context,
                "chunk_count": claimed.chunk_count,
                "model": config.model,
                "revision": config.revision[:12],
            },
        )
        if claimed.attempts > self._settings.embedding_max_attempts:
            return await self._finish_failed(
                claimed.id, _Failure(FailureReason.RETRIES_EXHAUSTED), context
            )
        if claimed.chunk_count == 0:
            return await self._finish_failed(
                claimed.id, _Failure(FailureReason.EMPTY_DOCUMENT, detail="no chunks"), context
            )
        try:
            return await self._run(claimed, config_id, context)
        except _Failure as failure:
            await self._session.rollback()
            return await self._handle_failure(claimed, failure, context)
        except IntegrityError:
            # The document (or its chunks) disappeared while we were writing: nothing to save.
            await self._session.rollback()
            logger.info("embedding discarded", extra={**context, "reason": "document_changed"})
            return EmbeddingOutcome("skipped", "document_changed")
        except SQLAlchemyError as error:
            await self._session.rollback()
            db_failure = _Failure(
                FailureReason.DATABASE_ERROR, retryable=True, detail=type(error).__name__
            )
            return await self._handle_failure(claimed, db_failure, context)
        except EmbeddingError as error:
            await self._session.rollback()
            return await self._handle_failure(claimed, _classify(error), context)
        except SoftTimeLimitExceeded:
            raise  # the task layer turns a time limit into a retry, not a failure
        except Exception as error:
            await self._session.rollback()
            # type only: a library message could contain input text
            logger.error(
                "unexpected embedding error",
                extra={**context, "error_type": type(error).__name__},
            )
            unexpected = _Failure(FailureReason.EMBEDDING_FAILED, detail=type(error).__name__)
            return await self._handle_failure(claimed, unexpected, context)

    async def handle_timeout(
        self, document_id: uuid.UUID, *, task_id: str = "-"
    ) -> EmbeddingOutcome:
        """The task hit its time limit mid-batch: retry within the budget, else fail."""
        await self._session.rollback()
        state = await self._documents.embedding_state(document_id)
        if state is None or state[0] != "embedding":
            return EmbeddingOutcome("skipped", "not_embedding")
        failure = _Failure(FailureReason.TIMEOUT, retryable=True)
        claimed = ClaimedEmbedding(document_id, uuid.UUID(int=0), 0, state[1])
        return await self._handle_failure(
            claimed, failure, {"document_id": str(document_id), "task_id": task_id}
        )

    # --- the run ------------------------------------------------------------------------

    async def _run(
        self, claimed: ClaimedEmbedding, config_id: uuid.UUID, context: dict[str, Any]
    ) -> EmbeddingOutcome:
        settings = self._settings
        started = time.monotonic()
        counters = _Run()

        await asyncio.to_thread(self._provider.warmup)
        # the budget covers embedding work only; loading the model has its own allowance
        budget_ends = time.monotonic() + settings.embedding_task_budget_seconds
        dropped = await self._embeddings.drop_stale(claimed.id, config_id)
        if dropped:
            logger.info("stale vectors dropped", extra={**context, "count": dropped})
        embedded = await self._embeddings.count_for(claimed.id, config_id)
        await self._session.commit()

        while True:
            pending = await self._embeddings.next_batch(
                claimed.id, config_id, settings.embedding_batch_size
            )
            if not pending:
                break
            if time.monotonic() >= budget_ends:
                return await self._yield_budget(claimed, embedded, counters, started, context)
            embedded = await self._embed_batch(claimed, config_id, pending, counters)
            if not await self._documents.record_embedding_progress(claimed.id, embedded=embedded):
                await self._session.rollback()
                logger.info("embedding discarded", extra={**context, "reason": "document_changed"})
                return EmbeddingOutcome("skipped", "document_changed")
            await self._session.commit()

        if not await self._documents.complete_embedding(claimed.id, config_id=config_id):
            await self._session.rollback()
            return await self._not_completed(claimed, context)
        await self._embeddings.delete_other_configs(claimed.id, config_id)
        await self._session.commit()
        self._log_done("embedding completed", context, counters, started, embedded, config_id)
        return EmbeddingOutcome("ready")

    async def _embed_batch(
        self,
        claimed: ClaimedEmbedding,
        config_id: uuid.UUID,
        pending: list[PendingChunk],
        counters: _Run,
    ) -> int:
        include_heading = self._provider.config.include_heading
        inputs = [
            passage_input(chunk.heading_path, chunk.text, include_heading=include_heading)
            for chunk in pending
        ]
        model_started = time.monotonic()
        batch = await asyncio.to_thread(self._provider.embed_documents, inputs)
        counters.model_seconds += time.monotonic() - model_started
        if batch.vectors.shape[0] != len(pending):
            raise InvalidEmbeddingError("provider returned a different number of vectors")
        flags = batch.truncated or (False,) * len(pending)
        rows = [
            VectorRow(chunk.id, vector.tolist(), chunk.text_sha256, bool(flag))
            for chunk, vector, flag in zip(pending, batch.vectors, flags, strict=True)
        ]
        await self._embeddings.insert_batch(claimed.id, claimed.user_id, config_id, rows)
        counters.batches += 1
        counters.chunks += len(rows)
        counters.truncated += batch.truncated_count
        return await self._embeddings.count_for(claimed.id, config_id)

    async def _yield_budget(
        self,
        claimed: ClaimedEmbedding,
        embedded: int,
        counters: _Run,
        started: float,
        context: dict[str, Any],
    ) -> EmbeddingOutcome:
        """Out of time with work left: release the document so a fresh task continues."""
        released = await self._documents.release_embedding(claimed.id)
        await self._session.commit()
        self._log_done("embedding paused", context, counters, started, embedded, None)
        return EmbeddingOutcome("continue" if released else "skipped")

    async def _not_completed(
        self, claimed: ClaimedEmbedding, context: dict[str, Any]
    ) -> EmbeddingOutcome:
        state = await self._documents.embedding_state(claimed.id)
        await self._session.rollback()
        if state is None or state[0] != "embedding":
            logger.info("embedding discarded", extra={**context, "reason": "document_changed"})
            return EmbeddingOutcome("skipped", "document_changed")
        # Still ours but the vector count does not match the chunk count: an inconsistency.
        return await self._finish_failed(
            claimed.id,
            _Failure(FailureReason.EMBEDDING_FAILED, detail="vector count mismatch"),
            context,
        )

    # --- failures -----------------------------------------------------------------------

    async def _handle_failure(
        self, claimed: ClaimedEmbedding, failure: _Failure, context: dict[str, Any]
    ) -> EmbeddingOutcome:
        # progress resets the stored counter, so judge by the current value, not the claim's
        state = await self._documents.embedding_state(claimed.id)
        attempts = state[1] if state is not None else claimed.attempts
        if failure.retryable and attempts < self._settings.embedding_max_attempts:
            released = await self._documents.release_embedding(claimed.id)
            await self._session.commit()
            delay = self._backoff(attempts)
            logger.warning(
                "embedding will be retried",
                extra={**context, "failure_reason": failure.reason.value, "retry_in": delay},
            )
            return EmbeddingOutcome("retry" if released else "skipped", failure.reason.value, delay)
        return await self._finish_failed(claimed.id, failure, context)

    async def _finish_failed(
        self, document_id: uuid.UUID, failure: _Failure, context: dict[str, Any]
    ) -> EmbeddingOutcome:
        reason = failure.reason
        await self._documents.fail_embedding(
            document_id, reason=reason.value, message=SAFE_MESSAGES[reason]
        )
        await self._session.commit()
        logger.warning(
            "embedding failed",
            extra={**context, "failure_reason": reason.value, "detail": failure.detail},
        )
        return EmbeddingOutcome("failed", reason.value)

    def _backoff(self, attempts: int) -> int:
        base = self._settings.embedding_retry_backoff_seconds
        return int(min(base * 2 ** max(attempts - 1, 0), MAX_BACKOFF_SECONDS))

    def _log_done(
        self,
        message: str,
        context: dict[str, Any],
        counters: _Run,
        started: float,
        embedded: int,
        config_id: uuid.UUID | None,
    ) -> None:
        elapsed = time.monotonic() - started
        throughput = counters.chunks / counters.model_seconds if counters.model_seconds else 0.0
        logger.info(
            message,
            extra={
                **context,
                "embedded_total": embedded,
                "chunks_this_run": counters.chunks,
                "batches": counters.batches,
                "truncated_chunks": counters.truncated,
                "model_seconds": round(counters.model_seconds, 2),
                "duration_ms": round(elapsed * 1000),
                "chunks_per_second": round(throughput, 1),
                "batch_size": self._settings.embedding_batch_size,
                "device": getattr(self._provider, "device", "n/a"),
            },
        )

    # --- recovery and maintenance -----------------------------------------------------------

    async def recover(self, enqueue: Callable[[uuid.UUID], None]) -> EmbeddingRecoveryReport:
        """Free documents whose embedding worker went quiet and queue lost work."""
        settings = self._settings
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=settings.embedding_stale_after_seconds)
        batch = settings.processing_sweep_batch_size
        released = abandoned = 0
        to_queue: list[uuid.UUID] = []

        for document_id, attempts in await self._documents.find_stalled_embedding(
            stale_before, batch
        ):
            if attempts >= settings.embedding_max_attempts:
                reason = FailureReason.RETRIES_EXHAUSTED
                if await self._documents.fail_embedding(
                    document_id,
                    reason=reason.value,
                    message=SAFE_MESSAGES[reason],
                    stale_before=stale_before,
                ):
                    abandoned += 1
            elif await self._documents.release_embedding(document_id, stale_before=stale_before):
                released += 1
                to_queue.append(document_id)

        idle_since = now - timedelta(seconds=settings.embedding_requeue_after_seconds)
        waiting = await self._documents.find_awaiting_embedding(idle_since, batch)
        to_queue.extend(i for i in waiting if i not in to_queue)
        await self._documents.touch(to_queue)
        await self._session.commit()

        requeued = 0
        for document_id in to_queue:
            try:
                enqueue(document_id)
                requeued += 1
            except Exception:
                logger.warning("could not queue embedding", extra={"document_id": str(document_id)})
        if released or abandoned or requeued:
            logger.warning(
                "embedding recovery sweep acted",
                extra={"released": released, "abandoned": abandoned, "requeued": requeued},
            )
        return EmbeddingRecoveryReport(released, abandoned, requeued)

    async def reindex(
        self, enqueue: Callable[[uuid.UUID], None], *, limit: int, include_failed: bool = False
    ) -> int:
        """Queue documents embedded under another configuration (explicit, never automatic)."""
        config_id = await self._embeddings.ensure_config(self._provider.config)
        candidates = await self._documents.find_for_reindex(
            config_id, limit, include_failed=include_failed
        )
        queued: list[uuid.UUID] = []
        for document_id in candidates:
            state = await self._documents.embedding_state(document_id)
            is_failed = state is not None and state[0] == "failed"
            if is_failed and not await self._documents.reset_failed_embedding(document_id):
                continue
            queued.append(document_id)
        await self._session.commit()
        for document_id in queued:
            enqueue(document_id)
        if queued:
            logger.warning("reindex queued", extra={"documents": len(queued)})
        return len(queued)


def _classify(error: EmbeddingError) -> _Failure:
    if isinstance(error, ModelUnavailableError):
        return _Failure(
            FailureReason.MODEL_UNAVAILABLE, retryable=error.retryable, detail=type(error).__name__
        )
    if isinstance(error, InvalidEmbeddingError):
        return _Failure(FailureReason.INVALID_EMBEDDING, detail=type(error).__name__)
    return _Failure(
        FailureReason.EMBEDDING_FAILED, retryable=error.retryable, detail=type(error).__name__
    )
