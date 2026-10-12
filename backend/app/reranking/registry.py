import threading
from collections.abc import Callable

from app.config import Settings
from app.reranking.cross_encoder import CrossEncoderReranker, RerankRuntime
from app.reranking.types import Reranker, RerankerConfig, RerankerUnavailableError

RerankerFactory = Callable[[RerankerConfig, RerankRuntime], Reranker]

_FACTORIES: dict[str, RerankerFactory] = {"cross_encoder": CrossEncoderReranker}
_CACHE: dict[tuple[RerankerConfig, RerankRuntime], Reranker] = {}
_LOCK = threading.Lock()


def register_reranker(name: str, factory: RerankerFactory) -> None:
    """Add a reranker implementation (tests register deterministic fakes here)."""
    _FACTORIES[name] = factory
    reset_reranker_cache()


def unregister_reranker(name: str) -> None:
    _FACTORIES.pop(name, None)
    reset_reranker_cache()


def reset_reranker_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def reranker_config(settings: Settings) -> RerankerConfig:
    return RerankerConfig(
        provider=settings.rerank_provider,
        model=settings.rerank_model,
        revision=settings.rerank_model_revision,
        max_length=settings.rerank_max_length,
    )


def rerank_runtime(settings: Settings) -> RerankRuntime:
    return RerankRuntime(
        device=settings.rerank_device,
        batch_size=settings.rerank_batch_size,
        cache_dir=settings.rerank_cache_dir,
        allow_download=settings.rerank_allow_download,
        torch_threads=settings.rerank_torch_threads,
    )


def get_reranker(settings: Settings) -> Reranker:
    """One reranker (and so one loaded model) per process and configuration. Nothing is loaded
    until it is first used."""
    config, runtime = reranker_config(settings), rerank_runtime(settings)
    with _LOCK:
        reranker = _CACHE.get((config, runtime))
        if reranker is None:
            factory = _FACTORIES.get(config.provider)
            if factory is None:
                raise RerankerUnavailableError(
                    f"Unknown RERANK_PROVIDER '{config.provider}'; available: "
                    + ", ".join(sorted(_FACTORIES))
                )
            reranker = factory(config, runtime)
            _CACHE[(config, runtime)] = reranker
    return reranker
