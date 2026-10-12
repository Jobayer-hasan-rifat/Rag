import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

Vectors = NDArray[np.float32]

# Bump when the way text is turned into model input changes (prefixes, heading handling, ...).
PREPROCESSING_VERSION = "e1"


class EmbeddingError(Exception):
    """Base class. `retryable` tells the pipeline whether trying again could help."""

    retryable = False


class ModelUnavailableError(EmbeddingError):
    """The model could not be loaded (missing weights, no network for a first download, ...)."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingFailedError(EmbeddingError):
    """Inference failed (out of memory, runtime error)."""

    retryable = True


class InvalidEmbeddingError(EmbeddingError):
    """The provider returned something unusable: wrong shape or dimension, NaN, or all zeros."""


@dataclass(frozen=True)
class EmbeddingConfig:
    """Everything that decides which vectors a model produces.

    Two configurations with the same fingerprint produce comparable vectors. Anything that only
    affects speed (batch size, device, threads) is runtime configuration and is not part of it.
    """

    provider: str
    model: str
    revision: str
    dimension: int
    metric: Literal["cosine"] = "cosine"
    normalized: bool = True
    query_prefix: str = ""
    passage_prefix: str = ""
    max_seq_length: int = 512
    include_heading: bool = False
    preprocessing_version: str = PREPROCESSING_VERSION

    def identity(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "dimension": self.dimension,
            "metric": self.metric,
            "normalized": self.normalized,
            "query_prefix": self.query_prefix,
            "passage_prefix": self.passage_prefix,
            "max_seq_length": self.max_seq_length,
            "include_heading": self.include_heading,
            "preprocessing_version": self.preprocessing_version,
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.identity(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: Vectors  # shape (n, dimension), float32
    # One flag per input: longer than the model's sequence limit, so its tail was ignored
    truncated: tuple[bool, ...] = ()

    @property
    def truncated_count(self) -> int:
        return sum(self.truncated)


class EmbeddingProvider(Protocol):
    """Turns text into vectors. Implementations are synchronous and CPU/GPU bound.

    Callers in async code must run these methods in a worker thread. Implementations must never
    return placeholder vectors: an unusable result raises an `EmbeddingError`.
    """

    config: EmbeddingConfig

    def warmup(self) -> None:
        """Load the model now (raises ModelUnavailableError) so failures surface early."""
        ...

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch: ...

    def embed_query(self, text: str) -> Vectors:
        """One query vector with shape (dimension,)."""
        ...
