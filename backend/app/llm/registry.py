import threading
from collections.abc import Callable
from urllib.parse import urlparse

from app.config import Settings
from app.llm.http_providers import OllamaProvider, OpenAICompatibleProvider
from app.llm.types import LLMConfig, LLMProvider, LLMUnavailableError

ProviderFactory = Callable[[LLMConfig, str | None], LLMProvider]

_FACTORIES: dict[str, ProviderFactory] = {
    "ollama": lambda config, key: OllamaProvider(config, key),
    "openai_compatible": lambda config, key: OpenAICompatibleProvider(config, key),
}
_CACHE: dict[tuple[LLMConfig, str | None], LLMProvider] = {}
_LOCK = threading.Lock()
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})


def register_llm_provider(name: str, factory: ProviderFactory) -> None:
    """Add a provider (tests register deterministic fakes here)."""
    _FACTORIES[name] = factory
    reset_llm_cache()


def unregister_llm_provider(name: str) -> None:
    _FACTORIES.pop(name, None)
    reset_llm_cache()


def reset_llm_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def llm_config(settings: Settings) -> LLMConfig:
    return LLMConfig(
        provider=settings.llm_provider,
        base_url=settings.llm_base_url.rstrip("/"),
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
        context_tokens=settings.llm_context_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        structured_output=settings.llm_structured_output,
    )


def is_local_endpoint(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "") in LOOPBACK_HOSTS


def get_llm_provider(settings: Settings) -> LLMProvider:
    """One provider per process and configuration. It is created on first use; nothing is
    contacted until a request is generated. Raises LLMUnavailableError for an unknown provider."""
    config = llm_config(settings)
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    with _LOCK:
        provider = _CACHE.get((config, key))
        if provider is None:
            factory = _FACTORIES.get(config.provider)
            if factory is None:
                raise LLMUnavailableError(
                    f"Unknown LLM_PROVIDER '{config.provider}'; available: "
                    + ", ".join(sorted(_FACTORIES))
                )
            provider = factory(config, key)
            _CACHE[(config, key)] = provider
    return provider
