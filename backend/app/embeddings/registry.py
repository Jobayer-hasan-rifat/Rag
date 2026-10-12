import threading
from collections.abc import Callable

from app.config import Settings
from app.embeddings.sentence_transformer import RuntimeSettings, SentenceTransformerProvider
from app.embeddings.types import EmbeddingConfig, EmbeddingProvider, ModelUnavailableError

ProviderFactory = Callable[[EmbeddingConfig, RuntimeSettings], EmbeddingProvider]

_FACTORIES: dict[str, ProviderFactory] = {"sentence_transformers": SentenceTransformerProvider}
_CACHE: dict[tuple[str, RuntimeSettings], EmbeddingProvider] = {}
_LOCK = threading.Lock()


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Add a provider. A hosted API client would register itself here."""
    _FACTORIES[name] = factory
    reset_provider_cache()


def unregister_provider(name: str) -> None:
    _FACTORIES.pop(name, None)
    reset_provider_cache()


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def reset_provider_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def embedding_config(settings: Settings) -> EmbeddingConfig:
    return EmbeddingConfig(
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        revision=settings.embedding_model_revision,
        dimension=settings.embedding_dimension,
        normalized=settings.embedding_normalize,
        query_prefix=settings.embedding_query_prefix,
        passage_prefix=settings.embedding_passage_prefix,
        max_seq_length=settings.embedding_max_seq_length,
        include_heading=settings.embedding_include_heading,
    )


def runtime_settings(settings: Settings) -> RuntimeSettings:
    return RuntimeSettings(
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        cache_dir=settings.embedding_cache_dir,
        allow_download=settings.embedding_allow_download,
        torch_threads=settings.embedding_torch_threads,
    )


def get_provider(settings: Settings) -> EmbeddingProvider:
    """One provider (and so one loaded model) per process and configuration."""
    config = embedding_config(settings)
    runtime = runtime_settings(settings)
    key = (config.fingerprint(), runtime)
    with _LOCK:
        provider = _CACHE.get(key)
        if provider is None:
            factory = _FACTORIES.get(config.provider)
            if factory is None:
                raise ModelUnavailableError(
                    f"Unknown EMBEDDING_PROVIDER '{config.provider}'; "
                    f"available: {', '.join(available_providers())}"
                )
            provider = factory(config, runtime)
            _CACHE[key] = provider
    return provider
