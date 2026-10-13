"""Generation providers that speak HTTP: a local Ollama server and any OpenAI-compatible API.

Neither needs a vendor SDK. A fresh `httpx.AsyncClient` is used per call, so a provider has no
connection state tied to an event loop and can be shared by the whole process. Provider response
bodies and exception texts are never logged or forwarded: they can echo the prompt.
"""

import json
import time
from typing import Any

import httpx

from app.llm.types import (
    GenerationRequest,
    GenerationResult,
    LLMBadResponseError,
    LLMConfig,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.observability.logging import get_logger

logger = get_logger("app.llm")


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after", "")
    return int(value) if value.isdigit() and int(value) <= 3600 else None


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class _HttpProvider:
    """Shared request plumbing; subclasses build the body and read the answer."""

    def __init__(
        self,
        config: LLMConfig,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self._transport = transport  # tests inject a mock transport here

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        timeout = httpx.Timeout(
            self.config.timeout_seconds, connect=min(10, self.config.timeout_seconds)
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=timeout,
                transport=self._transport,
                trust_env=False,  # never route prompts through an environment proxy
            ) as client:
                response = await client.post(path, json=body, headers=self._headers())
        except httpx.TimeoutException:
            logger.warning("generation timed out", extra={"provider": self.config.provider})
            raise LLMTimeoutError("The generation provider timed out") from None
        except httpx.HTTPError as error:
            logger.error(
                "generation provider unreachable",
                extra={"provider": self.config.provider, "error_type": type(error).__name__},
            )
            raise LLMUnavailableError("The generation provider could not be reached") from None
        self._check_status(response)
        return response

    def _check_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 300:
            return
        logger.error(
            "generation provider returned an error",
            extra={"provider": self.config.provider, "status": status},
        )
        if status == 429:
            raise LLMRateLimitedError(
                "The generation provider is rate limiting requests",
                retry_after=_retry_after(response),
            )
        if status in (401, 403):
            raise LLMUnavailableError("The generation provider rejected the credentials")
        if status == 404:
            raise LLMUnavailableError("The generation model or endpoint was not found")
        if status >= 500:
            raise LLMUnavailableError("The generation provider failed")
        # anything else, redirects included, is not a completion
        raise LLMBadResponseError("The generation provider rejected the request")

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            raise LLMBadResponseError("The generation provider returned invalid JSON") from None
        if not isinstance(data, dict):
            raise LLMBadResponseError("The generation provider returned an unexpected payload")
        return data


class OllamaProvider(_HttpProvider):
    """A local Ollama server (`/api/chat`): no API key, no cloud, the model stays on the machine.

    `num_ctx` is always sent, so the server cannot silently truncate the prompt to a smaller
    default window. `think: false` stops reasoning models from spending the output budget on
    hidden thoughts.
    """

    _no_think = False

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        config = self.config
        body: dict[str, Any] = {
            "model": config.model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "options": {
                "temperature": config.temperature,
                "num_predict": request.max_output_tokens,
                "num_ctx": config.context_tokens,
            },
        }
        if request.json_schema is not None and config.structured_output != "none":
            body["format"] = (
                request.json_schema if config.structured_output == "json_schema" else "json"
            )
        if self._no_think:
            body.pop("think")
        started = time.monotonic()
        try:
            response = await self._post("/api/chat", body)
        except LLMBadResponseError:
            if "think" not in body:
                raise
            # a model that cannot "think" may reject the flag: remember it and retry once
            self._no_think = True
            body.pop("think")
            response = await self._post("/api/chat", body)
        data = self._json(response)
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMBadResponseError("The generation provider returned no message")
        return GenerationResult(
            text=content,
            input_tokens=_as_int(data.get("prompt_eval_count")),
            output_tokens=_as_int(data.get("eval_count")),
            finish_reason=(
                data.get("done_reason") if isinstance(data.get("done_reason"), str) else None
            ),
            latency_ms=round((time.monotonic() - started) * 1000),
        )


class OpenAICompatibleProvider(_HttpProvider):
    """Any server that implements `POST {base_url}/chat/completions` (OpenAI, LM Studio, vLLM, ...).

    This is the hosted option: the question and the retrieved passages leave the machine for the
    configured endpoint, so it is only used when `LLM_PROVIDER=openai_compatible` is set explicitly.
    """

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        config = self.config
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": config.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.json_schema is not None:
            if config.structured_output == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "grounded_answer",
                        "strict": True,
                        "schema": request.json_schema,
                    },
                }
            elif config.structured_output == "json_object":
                body["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        data = self._json(await self._post("/chat/completions", body))
        choices = data.get("choices")
        first: dict[str, Any] = choices[0] if isinstance(choices, list) and choices else {}
        if not isinstance(first, dict):
            raise LLMBadResponseError("The generation provider returned an unexpected payload")
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMBadResponseError("The generation provider returned no message")
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        return GenerationResult(
            text=content,
            input_tokens=_as_int(usage.get("prompt_tokens")),
            output_tokens=_as_int(usage.get("completion_tokens")),
            finish_reason=(
                first.get("finish_reason") if isinstance(first.get("finish_reason"), str) else None
            ),
            latency_ms=round((time.monotonic() - started) * 1000),
        )


def dump(data: object) -> str:
    """Compact JSON for tests and diagnostics (never used on provider payloads in logs)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
