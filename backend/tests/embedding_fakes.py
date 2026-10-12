"""Deterministic embedding providers for tests. The application never ships these."""

import hashlib
import re
import threading
import unicodedata
from collections.abc import Callable

import numpy as np

from app.embeddings.constants import VECTOR_DIMENSION
from app.embeddings.sentence_transformer import RuntimeSettings
from app.embeddings.types import (
    EmbeddingBatch,
    EmbeddingConfig,
    EmbeddingFailedError,
    ModelUnavailableError,
    Vectors,
)
from app.embeddings.validation import validate_vectors

_TOKEN = re.compile(r"[\wঀ-৿]+", re.UNICODE)


def hashed_vector(text: str, dimension: int = VECTOR_DIMENSION) -> Vectors:
    """A unit vector from hashed words, so texts that share words are genuinely closer.

    Words are lower-cased and NFC-normalised; each word adds +1 or -1 to a hashed coordinate.
    """
    vector = np.zeros(dimension, dtype=np.float32)
    words = _TOKEN.findall(unicodedata.normalize("NFC", text).lower())
    for word in words or ["<empty>"]:
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:  # opposite signs cancelled on one coordinate
        vector[0] = 1.0
        norm = 1.0
    return (vector / norm).astype(np.float32)


class HashProvider:
    """Real-shaped provider: validates like the production one, embeds with hashed words."""

    def __init__(self, config: EmbeddingConfig, runtime: RuntimeSettings | None = None) -> None:
        self.config = config
        self.device = "fake"
        self.calls: list[int] = []  # batch sizes seen by embed_documents
        self.warmups = 0
        self._lock = threading.Lock()

    def warmup(self) -> None:
        self.warmups += 1

    def _vectors(self, texts: list[str]) -> Vectors:
        if not texts:
            return np.zeros((0, self.config.dimension), dtype=np.float32)
        matrix = np.vstack([hashed_vector(t, self.config.dimension) for t in texts])
        return validate_vectors(matrix, self.config, expected=len(texts))

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        with self._lock:
            self.calls.append(len(texts))
        prefixed = [self.config.passage_prefix + t for t in texts]
        flags = tuple(len(t) > 2000 for t in texts)  # pretend very long chunks get truncated
        return EmbeddingBatch(self._vectors(prefixed), truncated=flags)

    def embed_query(self, text: str) -> Vectors:
        vector: Vectors = self._vectors([self.config.query_prefix + text])[0]
        return vector


class ControlledProvider(HashProvider):
    """Returns exactly the vectors a test dictates (text -> vector), for ranking assertions."""

    def __init__(
        self, config: EmbeddingConfig, table: dict[str, list[float]], runtime: object = None
    ) -> None:
        super().__init__(config)
        self._table = table

    def _vectors(self, texts: list[str]) -> Vectors:
        rows = []
        for text in texts:
            raw = text
            for prefix in (self.config.passage_prefix, self.config.query_prefix):
                if prefix and raw.startswith(prefix):
                    raw = raw[len(prefix) :]
                    break
            vector = np.zeros(self.config.dimension, dtype=np.float32)
            values = self._table[raw]
            vector[: len(values)] = values
            rows.append(vector / np.linalg.norm(vector))
        return validate_vectors(np.vstack(rows), self.config, expected=len(texts))


class FailingProvider(HashProvider):
    """Fails on demand: the first `failures` embed_documents calls raise `error`."""

    def __init__(
        self,
        config: EmbeddingConfig,
        runtime: RuntimeSettings | None = None,
        *,
        failures: int = 10**9,
        error: Callable[[], Exception] | None = None,
        warmup_error: Exception | None = None,
    ) -> None:
        super().__init__(config)
        self._remaining = failures
        self._error = error or (lambda: EmbeddingFailedError("simulated inference failure"))
        self._warmup_error = warmup_error

    def warmup(self) -> None:
        if self._warmup_error is not None:
            raise self._warmup_error
        super().warmup()

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error()
        return super().embed_documents(texts)


def unavailable(retryable: bool = False) -> ModelUnavailableError:
    return ModelUnavailableError("simulated: model missing", retryable=retryable)


class SlowProvider(HashProvider):
    """Sleeps per batch so a test can run out of time budget between batches."""

    def __init__(
        self, config: EmbeddingConfig, runtime: RuntimeSettings | None = None, delay: float = 0.4
    ) -> None:
        super().__init__(config)
        self._delay = delay

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        import time

        time.sleep(self._delay)
        return super().embed_documents(texts)


class HookProvider(HashProvider):
    """Runs a callback (in the worker thread) just before each batch is embedded."""

    def __init__(
        self,
        config: EmbeddingConfig,
        before_batch: Callable[[int], None],
        runtime: RuntimeSettings | None = None,
    ) -> None:
        super().__init__(config)
        self._before_batch = before_batch

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        self._before_batch(len(self.calls))
        return super().embed_documents(texts)


class BadVectorProvider(HashProvider):
    """Returns vectors a healthy provider never would (NaN, zeros, wrong dimension)."""

    def __init__(
        self, config: EmbeddingConfig, runtime: RuntimeSettings | None = None, kind: str = "nan"
    ) -> None:
        super().__init__(config)
        self._kind = kind

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        count = len(texts)
        if self._kind == "nan":
            vectors = np.full((count, self.config.dimension), np.nan, dtype=np.float32)
        elif self._kind == "zero":
            vectors = np.zeros((count, self.config.dimension), dtype=np.float32)
        else:
            vectors = np.ones((count, self.config.dimension + 3), dtype=np.float32)
        return EmbeddingBatch(validate_vectors(vectors, self.config, expected=count))
