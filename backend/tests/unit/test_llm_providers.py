import asyncio
import json
import logging
from typing import Any

import httpx
import pytest

from app.llm.http_providers import OllamaProvider, OpenAICompatibleProvider
from app.llm.registry import get_llm_provider, is_local_endpoint, llm_config
from app.llm.types import (
    GenerationRequest,
    LLMBadResponseError,
    LLMConfig,
    LLMRateLimitedError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from tests.conftest import SettingsFactory

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}
REQUEST = GenerationRequest("SYSTEM RULES", "USER QUESTION", 123, SCHEMA)


def config(provider: str = "ollama", structured: str = "json_schema") -> LLMConfig:
    return LLMConfig(provider, "http://llm.test", "m1", 0.2, 400, 4096, 30, structured)


def ollama(handler: Any, **kw: Any) -> OllamaProvider:
    return OllamaProvider(config(**kw), None, httpx.MockTransport(handler))


def openai(handler: Any, key: str | None = "sk-secret-key", **kw: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config("openai_compatible", **kw), key, httpx.MockTransport(handler)
    )


def ok_ollama(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {"content": '{"answer":"x"}'},
            "prompt_eval_count": 90,
            "eval_count": 12,
            "done_reason": "stop",
        },
    )


def test_ollama_sends_the_bounded_request_and_reads_the_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return ok_ollama(request)

    result = asyncio.run(ollama(handler).generate(REQUEST))

    body = seen["body"]
    assert seen["url"] == "http://llm.test/api/chat" and seen["auth"] is None
    assert body["model"] == "m1" and body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0.2, "num_predict": 123, "num_ctx": 4096}
    assert body["format"] == SCHEMA  # the provider is asked to constrain the output
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "SYSTEM RULES"
    assert (result.text, result.input_tokens, result.output_tokens) == ('{"answer":"x"}', 90, 12)
    assert result.finish_reason == "stop"


@pytest.mark.parametrize(("mode", "expected"), [("json_object", "json"), ("none", None)])
def test_ollama_structured_output_modes(mode: str, expected: Any) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok_ollama(request)

    asyncio.run(ollama(handler, structured=mode).generate(REQUEST))

    assert bodies[0].get("format") == expected


def test_ollama_retries_once_without_think_when_the_model_rejects_it() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if "think" in bodies[-1]:
            return httpx.Response(400, json={"error": "does not support thinking"})
        return ok_ollama(request)

    result = asyncio.run(ollama(handler).generate(REQUEST))

    assert len(bodies) == 2 and "think" not in bodies[1] and result.text


def test_openai_compatible_sends_the_key_only_as_a_bearer_header() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 7},
            },
        )

    result = asyncio.run(openai(handler).generate(REQUEST))

    assert seen["auth"] == "Bearer sk-secret-key"
    assert "sk-secret-key" not in json.dumps(seen["body"])
    assert seen["url"] == "http://llm.test/chat/completions"
    assert seen["body"]["max_tokens"] == 123 and seen["body"]["temperature"] == 0.2
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert (result.text, result.input_tokens, result.output_tokens, result.finish_reason) == (
        "hi", 50, 7, "length",
    )  # fmt: skip


def test_openai_compatible_without_a_key_sends_no_authorization() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    result = asyncio.run(openai(handler, key=None, structured="none").generate(REQUEST))

    assert seen["auth"] is None and result.input_tokens is None  # usage absent: reported as unknown


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429, headers={"retry-after": "9"}), LLMRateLimitedError),
        (httpx.Response(401), LLMUnavailableError),
        (httpx.Response(403), LLMUnavailableError),
        (httpx.Response(404), LLMUnavailableError),
        (httpx.Response(500), LLMUnavailableError),
        (httpx.Response(503), LLMUnavailableError),
        (httpx.Response(400, text="bad"), LLMBadResponseError),
        (httpx.Response(200, text="not json"), LLMBadResponseError),
        (httpx.Response(200, json=[1, 2]), LLMBadResponseError),
        (httpx.Response(200, json={"choices": []}), LLMBadResponseError),
        (httpx.Response(200, json={"choices": [{"message": {}}]}), LLMBadResponseError),
    ],
)
def test_provider_failures_map_to_typed_errors_without_the_response_body(
    response: httpx.Response, error: type[Exception]
) -> None:
    with pytest.raises(error) as raised:
        asyncio.run(openai(lambda request: response).generate(REQUEST))

    assert "bad" not in str(raised.value) and "sk-secret-key" not in str(raised.value)


def test_a_rate_limit_carries_the_retry_after_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "9"})

    with pytest.raises(LLMRateLimitedError) as raised:
        asyncio.run(openai(handler).generate(REQUEST))

    assert raised.value.retry_after == 9


def test_timeouts_and_connection_errors_are_reported_without_library_text() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret details", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret details at 10.0.0.5", request=request)

    with pytest.raises(LLMTimeoutError) as timed_out:
        asyncio.run(ollama(slow).generate(REQUEST))
    with pytest.raises(LLMUnavailableError) as down:
        asyncio.run(ollama(refused).generate(REQUEST))

    assert "secret" not in str(timed_out.value) and "secret" not in str(down.value)
    assert timed_out.value.reason == "timeout" and down.value.reason == "unavailable"


def test_prompts_completions_and_keys_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="echo: USER QUESTION and sk-secret-key")

    with caplog.at_level(logging.DEBUG), pytest.raises(LLMUnavailableError):
        asyncio.run(openai(handler).generate(REQUEST))

    assert "USER QUESTION" not in caplog.text and "sk-secret-key" not in caplog.text


def test_the_registry_creates_one_provider_per_configuration(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(llm_provider="ollama")

    first, second = get_llm_provider(settings), get_llm_provider(settings)
    other = get_llm_provider(make_settings(llm_provider="ollama", llm_model="other"))

    assert first is second and first is not other and isinstance(first, OllamaProvider)


def test_an_unknown_provider_is_reported_not_guessed(make_settings: SettingsFactory) -> None:
    with pytest.raises(LLMUnavailableError, match="Unknown LLM_PROVIDER"):
        get_llm_provider(make_settings(llm_provider="no-such-provider"))


def test_the_config_never_contains_the_api_key(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        llm_provider="openai_compatible",
        llm_base_url="https://api.example.com/v1/",
        llm_api_key="sk-secret-key",
    )

    cfg = llm_config(settings)

    assert "sk-secret-key" not in repr(cfg) and cfg.base_url == "https://api.example.com/v1"
    assert "sk-secret-key" not in repr(settings)  # SecretStr


def test_local_endpoints_are_recognised() -> None:
    assert is_local_endpoint("http://localhost:11434")
    assert is_local_endpoint("http://127.0.0.1:1234/v1")
    assert not is_local_endpoint("https://api.example.com/v1")


def test_redirects_are_not_a_completion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://elsewhere.example/"})

    with pytest.raises(LLMBadResponseError):
        asyncio.run(openai(handler).generate(REQUEST))


def test_a_model_that_rejects_think_is_remembered_and_not_asked_twice() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if "think" in bodies[-1]:
            return httpx.Response(400, json={"error": "does not support thinking"})
        return ok_ollama(request)

    provider = ollama(handler)
    asyncio.run(provider.generate(REQUEST))
    asyncio.run(provider.generate(REQUEST))

    assert [("think" in b) for b in bodies] == [True, False, False]


def test_provider_calls_ignore_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return ok_ollama(request)

    asyncio.run(ollama(handler).generate(REQUEST))

    assert seen == ["llm.test"]
