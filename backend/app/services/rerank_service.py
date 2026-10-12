import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings
from app.observability.logging import get_logger
from app.reranking.ranking import Reranked, RerankInput, order_by_score, unique_candidates
from app.reranking.registry import get_reranker, reranker_config
from app.reranking.types import (
    InvalidRerankScoreError,
    Reranker,
    RerankerError,
    RerankerTimeoutError,
    RerankerUnavailableError,
    ScoredBatch,
)

logger = get_logger("app.reranking")

FallbackReason = Literal["unavailable", "busy", "timeout", "failed", "invalid"]


@dataclass(frozen=True)
class RerankOutcome:
    """The result of one reranking attempt. When `applied` is false the caller must keep the
    retrieval order and say so: `items` is then empty and `fallback_reason` explains why."""

    applied: bool
    items: list[Reranked[uuid.UUID]] = field(default_factory=list)
    fallback_reason: FallbackReason | None = None
    candidates: int = 0
    truncated: int = 0
    latency_ms: int = 0
    load_ms: int = 0  # model load time inside this call: 0 when the model was already warm


class RerankService:
    """Scores authorised candidates with a cross-encoder in a worker thread.

    The caller passes only candidates that retrieval already returned for the requesting user;
    this service never loads data. At most `RERANK_MAX_CONCURRENT` scorings run at once, a slot is
    held until the thread really finishes (an abandoned request cannot pile up work), and the
    scoring loop stops between batches once its deadline passes. Any failure becomes a reported
    fallback, never a fabricated score.
    """

    def __init__(self, gate: asyncio.Semaphore, settings: Settings) -> None:
        self._gate = gate
        self._settings = settings

    def _reranker(self) -> Reranker:
        """The process-wide reranker, created on first use so a misconfiguration only affects
        requests that ask for reranking. Raises RerankerUnavailableError."""
        return get_reranker(self._settings)

    def describe(self) -> tuple[str, str, str]:
        """Model, revision and device for the response; never raises."""
        config = reranker_config(self._settings)
        try:
            device = self._reranker().device
        except RerankerError:
            device = "unloaded"
        return config.model, config.revision, device

    def _score(self, query: str, passages: list[str]) -> tuple[ScoredBatch, int]:
        """Runs in a worker thread: load if needed, then score under a deadline."""
        reranker = self._reranker()
        load_started = time.monotonic()
        reranker.warmup()
        load_ms = round((time.monotonic() - load_started) * 1000)
        deadline = time.monotonic() + self._settings.rerank_timeout_seconds
        return reranker.score(query, passages, deadline=deadline), load_ms

    def _release_when_done(self, future: "asyncio.Future[tuple[ScoredBatch, int]]") -> None:
        self._gate.release()
        if not future.cancelled():
            future.exception()  # consume it: an abandoned call that fails later stays quiet

    async def rerank(
        self, query: str, candidates: Sequence[RerankInput[uuid.UUID]], *, top_k: int
    ) -> RerankOutcome:
        settings = self._settings
        unique = unique_candidates(candidates)
        if not unique:
            return RerankOutcome(applied=True)
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._gate.acquire(), timeout=settings.rerank_timeout_seconds)
        except TimeoutError:
            logger.warning("reranking is saturated")
            return RerankOutcome(applied=False, fallback_reason="busy", candidates=len(unique))
        try:
            future = asyncio.get_running_loop().run_in_executor(
                None, self._score, query, [c.text for c in unique]
            )
        except BaseException:
            self._gate.release()  # nothing was started, so nothing will release the slot later
            raise
        future.add_done_callback(self._release_when_done)
        total_timeout = settings.rerank_timeout_seconds
        if self.describe()[2] == "unloaded":  # the model still has to load: allow for it
            total_timeout += settings.rerank_load_timeout_seconds
        reason: FallbackReason
        try:
            batch, load_ms = await asyncio.wait_for(asyncio.shield(future), timeout=total_timeout)
            items = order_by_score(
                unique, [float(s) for s in batch.scores], batch.truncated, top_k=top_k
            )
        except (TimeoutError, RerankerTimeoutError):
            reason = "timeout"
        except RerankerUnavailableError as error:
            logger.error("reranker unavailable", extra={"retryable": error.retryable})
            reason = "unavailable"
        except (InvalidRerankScoreError, ValueError) as error:
            logger.error(
                "reranker returned unusable scores", extra={"detail": type(error).__name__}
            )
            reason = "invalid"
        except RerankerError as error:
            logger.error("reranking failed", extra={"error_type": type(error).__name__})
            reason = "failed"
        except Exception as error:  # a reranker implementation that broke its contract
            logger.error("reranking crashed", extra={"error_type": type(error).__name__})
            reason = "failed"
        else:
            latency_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "reranked",
                extra={
                    "candidates": len(unique),
                    "returned": len(items),
                    "truncated": sum(batch.truncated),
                    "latency_ms": latency_ms,
                    "load_ms": load_ms,
                },
            )
            return RerankOutcome(
                applied=True,
                items=items,
                candidates=len(unique),
                truncated=sum(batch.truncated),
                latency_ms=latency_ms,
                load_ms=load_ms,
            )
        return RerankOutcome(
            applied=False,
            fallback_reason=reason,
            candidates=len(unique),
            latency_ms=round((time.monotonic() - started) * 1000),
        )
