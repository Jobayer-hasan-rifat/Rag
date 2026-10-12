"""Deterministic rerankers for tests. The application never ships these."""

import threading
import time

import numpy as np

from app.reranking.cross_encoder import RerankRuntime
from app.reranking.types import (
    RerankerConfig,
    RerankerFailedError,
    RerankerTimeoutError,
    RerankerUnavailableError,
    ScoredBatch,
)

LONG_PASSAGE_CHARS = 400


class FakeReranker:
    """Scores a passage by how many of the query's words it contains (case-insensitive).

    Counts loads and records every call, so tests can prove when a model was (not) touched and
    what text reached it.
    """

    def __init__(self, config: RerankerConfig, runtime: RerankRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.loads = 0
        self.calls: list[tuple[str, list[str]]] = []
        self._loaded = False
        self._lock = threading.Lock()

    @property
    def device(self) -> str:
        return "cpu" if self._loaded else "unloaded"

    def warmup(self) -> None:
        with self._lock:
            if not self._loaded:
                self.loads += 1
                self._loaded = True

    def raw_scores(self, query: str, passages: list[str]) -> list[float]:
        words = set(query.lower().split())
        return [float(len(words & set(p.lower().split()))) for p in passages]

    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        self.warmup()
        self.calls.append((query, list(passages)))
        if deadline is not None and time.monotonic() > deadline:
            raise RerankerTimeoutError("deadline passed")
        scores = np.array(self.raw_scores(query, passages), dtype=np.float32)
        return ScoredBatch(scores, tuple(len(p) > LONG_PASSAGE_CHARS for p in passages))


class UnavailableReranker(FakeReranker):
    def warmup(self) -> None:
        raise RerankerUnavailableError("model missing", retryable=False)


class FailingReranker(FakeReranker):
    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        self.calls.append((query, list(passages)))
        raise RerankerFailedError("simulated inference failure")


class CrashingReranker(FakeReranker):
    """Raises a plain RuntimeError (not a RerankerError) while loading."""

    def warmup(self) -> None:
        raise RuntimeError("CUDA out of memory at /secret/path")


class DeadlineReranker(FakeReranker):
    """Reports that its deadline passed."""

    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        raise RerankerTimeoutError("deadline passed")


class NaNReranker(FakeReranker):
    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        scores = np.full(len(passages), np.nan, dtype=np.float32)
        return ScoredBatch(scores, tuple(False for _ in passages))


class ShortReranker(FakeReranker):
    """Returns one score too few."""

    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        scores = np.zeros(max(0, len(passages) - 1), dtype=np.float32)
        return ScoredBatch(scores, tuple(False for _ in passages))


class SlowReranker(FakeReranker):
    """Ignores the deadline and blocks, to exercise the caller's own timeout."""

    delay = 1.5

    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        time.sleep(self.delay)
        return super().score(query, passages, deadline=None)
