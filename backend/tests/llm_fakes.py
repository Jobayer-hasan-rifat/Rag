"""Deterministic generation providers for tests. The application never ships these."""

import asyncio
import json
import re
from collections.abc import Callable

from app.llm.types import (
    GenerationRequest,
    GenerationResult,
    LLMConfig,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUnavailableError,
)

_SOURCE = re.compile(r'<source id="(S\d+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)


def sources_of(request: GenerationRequest) -> list[tuple[str, str]]:
    """(label, text) pairs found in the user message, as the model would see them."""
    return _SOURCE.findall(request.user)


def reply(answer: str, citations: list[str] | None = None, answerable: bool = True) -> str:
    return json.dumps(
        {"answerable": answerable, "answer": answer, "citations": citations or []},
        ensure_ascii=False,
    )


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?।])\s", text.strip(), maxsplit=1)[0]


class FakeLLM:
    """Answers with the first sentence of the first source it is shown, cited as [S1].

    `script` replaces that behaviour: it receives the request and returns the completion text, or
    raises an `LLMError` to simulate a provider failure. Every request is recorded.
    """

    def __init__(self, config: LLMConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key
        self.requests: list[GenerationRequest] = []
        self.script: Callable[[GenerationRequest], str] | None = None
        self.on_call: Callable[[], None] | None = None  # runs while "generating"
        self.delay = 0.0  # seconds the call takes (without blocking the event loop)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.on_call is not None:
            self.on_call()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script is not None:
            text = self.script(request)
        else:
            sources = sources_of(request)
            if not sources:
                text = reply("", [], answerable=False)
            else:
                label, body = sources[0]
                text = reply(f"{first_sentence(body)} [{label}]", [label])
        return GenerationResult(
            text, input_tokens=111, output_tokens=22, finish_reason="stop", latency_ms=3
        )


def raising(error: Exception) -> Callable[[GenerationRequest], str]:
    def script(request: GenerationRequest) -> str:
        raise error

    return script


TIMEOUT = LLMTimeoutError("timed out")
UNAVAILABLE = LLMUnavailableError("down: secret-host-name")
RATE_LIMITED = LLMRateLimitedError("slow down", retry_after=7)
