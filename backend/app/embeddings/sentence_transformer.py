import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.embeddings.types import (
    EmbeddingBatch,
    EmbeddingConfig,
    EmbeddingFailedError,
    InvalidEmbeddingError,
    ModelUnavailableError,
    Vectors,
)
from app.embeddings.validation import validate_vectors
from app.observability.logging import get_logger

logger = get_logger("app.embeddings")


@dataclass(frozen=True)
class RuntimeSettings:
    """Knobs that change speed and where the model comes from, but not the vectors."""

    device: str = "auto"  # auto | cpu | cuda | mps
    batch_size: int = 16
    cache_dir: str | None = None
    allow_download: bool = True
    torch_threads: int | None = None


def resolve_device(requested: str) -> str:
    import torch

    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise ModelUnavailableError("EMBEDDING_DEVICE=cuda but CUDA is not available")
    return requested


class SentenceTransformerProvider:
    """Local embeddings with sentence-transformers.

    Safety rules, enforced here and not configurable: the model revision is pinned, only
    safetensors weights are loaded (no pickle), and remote code is never executed.
    """

    def __init__(self, config: EmbeddingConfig, runtime: RuntimeSettings) -> None:
        self.config = config
        self._runtime = runtime
        self._model: Any = None
        self._device = "unloaded"
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    @property
    def device(self) -> str:
        return self._device

    # --- loading -----------------------------------------------------------------------

    def warmup(self) -> None:
        self._load()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                self._model = self._load_model()
        return self._model

    def _load_model(self) -> Any:
        config, runtime = self.config, self._runtime
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ModelUnavailableError(
                "sentence-transformers and torch are required for local embeddings"
            ) from error
        if runtime.torch_threads:
            torch.set_num_threads(runtime.torch_threads)
        device = resolve_device(runtime.device)
        local_dir = Path(config.model).is_dir()
        try:
            model = SentenceTransformer(
                config.model,
                device=device,
                cache_folder=runtime.cache_dir,
                revision=None if local_dir else config.revision,
                trust_remote_code=False,
                local_files_only=local_dir or not runtime.allow_download,
                model_kwargs={"use_safetensors": True},
            )
        except Exception as error:
            # Never forward the library's message: it can contain local paths.
            logger.error(
                "embedding model could not be loaded",
                extra={"model": config.model, "error_type": type(error).__name__},
            )
            hint = "" if runtime.allow_download else " (downloads are disabled; download it first)"
            misconfigured = isinstance(error, (ValueError, TypeError))
            raise ModelUnavailableError(
                f"Embedding model '{config.model}' could not be loaded{hint}",
                retryable=runtime.allow_download and not misconfigured,
            ) from error

        native = int(
            getattr(model, "max_seq_length", config.max_seq_length) or config.max_seq_length
        )
        if config.max_seq_length > native:
            raise ModelUnavailableError(
                f"EMBEDDING_MAX_SEQ_LENGTH={config.max_seq_length} exceeds the model limit {native}"
            )
        model.max_seq_length = config.max_seq_length
        # the method was renamed in sentence-transformers 6; support both
        dimension_of = getattr(model, "get_embedding_dimension", None)
        if dimension_of is None:
            dimension_of = model.get_sentence_embedding_dimension
        actual = int(dimension_of() or 0)
        if actual != config.dimension:
            raise ModelUnavailableError(
                f"Model produces {actual}-dimensional vectors but EMBEDDING_DIMENSION="
                f"{config.dimension}"
            )
        self._device = device
        logger.info(
            "embedding model loaded",
            extra={
                "model": config.model,
                "revision": config.revision[:12],
                "dimension": actual,
                "device": device,
            },
        )
        return model

    # --- embedding ---------------------------------------------------------------------

    def _encode(self, texts: list[str]) -> Vectors:
        model = self._load()
        try:
            with self._infer_lock:
                raw = model.encode(
                    texts,
                    batch_size=self._runtime.batch_size,
                    normalize_embeddings=self.config.normalized,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
        except InvalidEmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingFailedError(f"Embedding failed ({type(error).__name__})") from error
        return validate_vectors(np.asarray(raw, dtype=np.float32), self.config, expected=len(texts))

    def _truncated_flags(self, texts: list[str]) -> tuple[bool, ...]:
        tokenizer = self._load().tokenizer
        limit = self.config.max_seq_length
        encoded = tokenizer(texts, add_special_tokens=True, truncation=False)["input_ids"]
        return tuple(len(ids) > limit for ids in encoded)

    def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(np.zeros((0, self.config.dimension), dtype=np.float32), ())
        inputs = [self.config.passage_prefix + text for text in texts]
        vectors = self._encode(inputs)
        try:
            flags = self._truncated_flags(inputs)
        except Exception as error:
            raise EmbeddingFailedError(f"Embedding failed ({type(error).__name__})") from error
        return EmbeddingBatch(vectors, truncated=flags)

    def embed_query(self, text: str) -> Vectors:
        vectors = self._encode([self.config.query_prefix + text])
        query: Vectors = vectors[0]
        return query
