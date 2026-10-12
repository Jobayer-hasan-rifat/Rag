import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.embeddings.sentence_transformer import resolve_device
from app.embeddings.types import ModelUnavailableError
from app.observability.logging import get_logger
from app.reranking.types import (
    InvalidRerankScoreError,
    RerankerConfig,
    RerankerFailedError,
    RerankerTimeoutError,
    RerankerUnavailableError,
    ScoredBatch,
)

logger = get_logger("app.reranking")

# Architectures accepted without further proof: plain sequence classifiers whose code ships
# inside the `transformers` library. Anything else (custom `auto_map` code) is refused.
ACCEPTED_ARCHITECTURES = frozenset(
    {
        "XLMRobertaForSequenceClassification",
        "BertForSequenceClassification",
        "RobertaForSequenceClassification",
        "DistilBertForSequenceClassification",
    }
)


@dataclass(frozen=True)
class RerankRuntime:
    """Knobs that change speed and where the model comes from, but not the scores."""

    device: str = "auto"
    batch_size: int = 8
    cache_dir: str | None = None
    allow_download: bool = True
    torch_threads: int | None = None


class CrossEncoderReranker:
    """A local Hugging Face sequence-classification cross-encoder.

    Safety rules, enforced here and not configurable: the revision is pinned, only safetensors
    weights are loaded (no pickle), remote code is never executed, and the architecture must be
    a plain single-output sequence classifier. The score is the model's single output logit;
    nothing is added (no softmax, no normalisation).

    Truncation policy: query and passage are tokenised together up to `max_length` tokens and the
    longest side is shortened first, so a long passage loses its *tail*. Every input that lost
    text is reported in `ScoredBatch.truncated` so callers can tell.
    """

    def __init__(self, config: RerankerConfig, runtime: RerankRuntime) -> None:
        self.config = config
        self._runtime = runtime
        self._model: Any = None
        self._tokenizer: Any = None
        self._device = "unloaded"
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._permanent_failure: RerankerUnavailableError | None = None

    @property
    def device(self) -> str:
        return self._device

    # --- loading -----------------------------------------------------------------------

    def warmup(self) -> None:
        self._load()

    def _load(self) -> tuple[Any, Any]:
        if self._model is None:
            with self._load_lock:
                if self._permanent_failure is not None:  # do not reload a model that cannot work
                    raise self._permanent_failure
                if self._model is None:
                    try:
                        self._tokenizer, self._model = self._load_model()
                    except RerankerUnavailableError as error:
                        if not error.retryable:
                            self._permanent_failure = error
                        raise
                    except Exception as error:
                        # Never forward the library's message: it can contain local paths.
                        logger.error(
                            "reranker model could not be set up",
                            extra={"model": self.config.model, "error_type": type(error).__name__},
                        )
                        raise RerankerUnavailableError(
                            f"Reranker model '{self.config.model}' could not be set up",
                            retryable=True,
                        ) from error
        return self._tokenizer, self._model

    def _load_model(self) -> tuple[Any, Any]:
        config, runtime = self.config, self._runtime
        try:
            import torch
            from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:
            raise RerankerUnavailableError("transformers and torch are required") from error
        if runtime.torch_threads:
            torch.set_num_threads(runtime.torch_threads)
        try:
            device = resolve_device(runtime.device)
        except ModelUnavailableError as error:
            raise RerankerUnavailableError(str(error)) from error
        local_dir = Path(config.model).is_dir()
        common: dict[str, Any] = {
            "revision": None if local_dir else config.revision,
            "trust_remote_code": False,
            "local_files_only": local_dir or not runtime.allow_download,
            "cache_dir": runtime.cache_dir,
        }
        try:
            model_config = AutoConfig.from_pretrained(config.model, **common)
            self._validate_architecture(model_config)
            limit = int(getattr(model_config, "max_position_embeddings", 512)) - 2
            if config.max_length > limit:  # checked before any weights are read
                raise RerankerUnavailableError(
                    f"RERANK_MAX_LENGTH={config.max_length} exceeds the model limit {limit}"
                )
            tokenizer = AutoTokenizer.from_pretrained(config.model, **common)
            model = AutoModelForSequenceClassification.from_pretrained(
                config.model, use_safetensors=True, config=model_config, **common
            )
        except RerankerUnavailableError:
            raise
        except Exception as error:
            # Never forward the library's message: it can contain local paths.
            logger.error(
                "reranker model could not be loaded",
                extra={"model": config.model, "error_type": type(error).__name__},
            )
            hint = "" if runtime.allow_download else " (downloads are disabled; download it first)"
            misconfigured = isinstance(error, (ValueError, TypeError))
            raise RerankerUnavailableError(
                f"Reranker model '{config.model}' could not be loaded{hint}",
                retryable=runtime.allow_download and not misconfigured,
            ) from error
        model.eval()
        model.to(device)
        self._device = device
        logger.info(
            "reranker model loaded",
            extra={"model": config.model, "revision": config.revision[:12], "device": device},
        )
        return tokenizer, model

    @staticmethod
    def _validate_architecture(model_config: Any) -> None:
        architectures = list(getattr(model_config, "architectures", None) or [])
        if getattr(model_config, "auto_map", None):
            raise RerankerUnavailableError("The model needs custom code, which is never executed")
        if not architectures or not set(architectures) <= ACCEPTED_ARCHITECTURES:
            raise RerankerUnavailableError(
                f"Unsupported reranker architecture {architectures or 'unknown'}"
            )
        if int(getattr(model_config, "num_labels", 0)) != 1:
            raise RerankerUnavailableError("A reranker must have exactly one output score")

    # --- scoring -----------------------------------------------------------------------

    def _exceeds_limit(self, tokenizer: Any, query: str, passages: list[str]) -> list[bool]:
        """Which pairs would be cut by the length limit."""
        full = tokenizer([query] * len(passages), passages, truncation=False)["input_ids"]
        return [len(ids) > self.config.max_length for ids in full]

    def score(
        self, query: str, passages: list[str], *, deadline: float | None = None
    ) -> ScoredBatch:
        if not passages:
            return ScoredBatch(np.zeros(0, dtype=np.float32), ())
        tokenizer, model = self._load()
        try:
            import torch

            truncated = self._exceeds_limit(tokenizer, query, passages)
            # shortest first keeps padding low; scores are put back in input order afterwards
            order = sorted(range(len(passages)), key=lambda i: len(passages[i]))
            scores = np.zeros(len(passages), dtype=np.float32)
            size = self._runtime.batch_size
            with self._infer_lock, torch.inference_mode():
                for start in range(0, len(order), size):
                    if deadline is not None and time.monotonic() > deadline:
                        raise RerankerTimeoutError("Reranking timed out")
                    chosen = order[start : start + size]
                    encoded = tokenizer(
                        [query] * len(chosen),
                        [passages[i] for i in chosen],
                        padding=True,
                        truncation="longest_first",
                        max_length=self.config.max_length,
                        return_tensors="pt",
                    ).to(self._device)
                    logits = model(**encoded).logits
                    if tuple(logits.shape) != (len(chosen), 1):
                        raise InvalidRerankScoreError("Unexpected reranker output shape")
                    scores[chosen] = logits[:, 0].float().cpu().numpy()
        except (RerankerTimeoutError, InvalidRerankScoreError):
            raise
        except Exception as error:
            raise RerankerFailedError(f"Reranking failed ({type(error).__name__})") from error
        if not all(math.isfinite(float(value)) for value in scores):
            raise InvalidRerankScoreError("The reranker returned a non-finite score")
        return ScoredBatch(scores, tuple(truncated))
