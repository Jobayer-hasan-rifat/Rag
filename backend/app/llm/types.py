"""Types and errors of the generation-provider layer.

A provider turns one prompt into one text completion. It knows nothing about retrieval,
citations or the database; the RAG pipeline owns those.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    """Base class. `retryable` says whether trying again later could help. The message is safe to
    log; it never contains the prompt, the completion or a provider response body."""

    retryable = False
    reason = "failed"


class LLMUnavailableError(LLMError):
    """The provider cannot be reached, rejected our credentials, or does not have the model."""

    retryable = True
    reason = "unavailable"


class LLMTimeoutError(LLMError):
    """The provider did not answer within the request timeout."""

    retryable = True
    reason = "timeout"


class LLMRateLimitedError(LLMError):
    """The provider (or its gateway) asked us to slow down."""

    retryable = True
    reason = "rate_limited"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMBadResponseError(LLMError):
    """The provider answered with something that is not a usable completion."""

    reason = "bad_response"


@dataclass(frozen=True)
class LLMConfig:
    """Everything that decides which completion a request gets. Never holds a credential."""

    provider: str
    base_url: str
    model: str
    temperature: float
    max_output_tokens: int
    context_tokens: int
    timeout_seconds: int
    structured_output: str  # json_schema | json_object | none


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    max_output_tokens: int
    json_schema: dict[str, Any] | None = None  # asks the provider to constrain the output


@dataclass(frozen=True)
class GenerationResult:
    text: str
    input_tokens: int | None  # as reported by the provider; None if it does not say
    output_tokens: int | None
    finish_reason: str | None
    latency_ms: int


class LLMProvider(Protocol):
    config: LLMConfig

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """One completion. Raises an `LLMError` subclass on any failure."""
