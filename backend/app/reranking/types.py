"""Types and errors of the reranking layer.

A reranker scores (query, passage) pairs jointly. Its output is a *raw relevance score* (the
model's logit): higher is more relevant for this query, the scale is the model's own, and it is
neither a probability nor comparable between queries or models. It is never stored.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

Scores = NDArray[np.float32]


class RerankerError(Exception):
    """Base class. `retryable` says whether trying again could help."""

    retryable = False


class RerankerUnavailableError(RerankerError):
    """The model could not be loaded or is not acceptable (missing files, wrong architecture)."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class RerankerFailedError(RerankerError):
    """Inference failed (out of memory, runtime error)."""

    retryable = True


class RerankerTimeoutError(RerankerError):
    """Scoring ran past its deadline and was abandoned between batches."""

    retryable = True


class InvalidRerankScoreError(RerankerError):
    """The model returned the wrong number of scores, or NaN or infinite values."""


@dataclass(frozen=True)
class RerankerConfig:
    """What decides which scores a model produces (and so what a result is comparable with)."""

    provider: str
    model: str
    revision: str
    max_length: int  # tokens of query + passage together


@dataclass(frozen=True)
class ScoredBatch:
    """Raw scores in input order and which inputs lost text to the length limit."""

    scores: Scores
    truncated: tuple[bool, ...]


class Reranker(Protocol):
    """Anything that can score passages for a query. Loading is separate from scoring."""

    config: RerankerConfig

    @property
    def device(self) -> str: ...

    def warmup(self) -> None:
        """Load the model (idempotent). Raises RerankerUnavailableError."""

    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        """One raw score per passage, in order. `deadline` is a `time.monotonic()` value checked
        between batches."""
