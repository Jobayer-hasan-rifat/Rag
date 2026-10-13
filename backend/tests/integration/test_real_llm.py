"""Tests against a real, local generation provider (Ollama).

Skipped unless RAG_REAL_LLM_TESTS=1, because they need a running Ollama server with the model
already pulled and take tens of seconds per call. Nothing is downloaded by the application:

    RAG_REAL_LLM_TESTS=1 RAG_REAL_LLM_MODEL=qwen2.5:latest \
        python -m pytest tests/integration/test_real_llm.py

The assertions are loose; answer quality is measured by `python -m evaluation.rag_eval`.
"""

import asyncio
import os

import pytest

from app.llm.http_providers import OllamaProvider
from app.llm.types import GenerationRequest, LLMConfig, LLMUnavailableError
from app.rag.grounding import ground_answer, parse_model_output
from app.rag.prompt import ANSWER_SCHEMA, build_user_message, load_system_prompt
from tests.unit.test_rag_context_and_prompt import builder, ranked

pytestmark = [
    pytest.mark.real_model,
    pytest.mark.skipif(
        os.environ.get("RAG_REAL_LLM_TESTS") != "1",
        reason="set RAG_REAL_LLM_TESTS=1 to run tests that call a real local model",
    ),
]

BASE_URL = os.environ.get("RAG_REAL_LLM_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("RAG_REAL_LLM_MODEL", "qwen2.5:latest")


def provider(timeout: int = 300) -> OllamaProvider:
    return OllamaProvider(
        LLMConfig("ollama", BASE_URL, MODEL, 0.1, 300, 4096, timeout, "json_schema")
    )


def ask(question: str, *sources: str) -> tuple[str, object]:
    blocks = builder().build(ranked(*sources)).blocks
    request = GenerationRequest(
        load_system_prompt(), build_user_message(question, blocks), 300, ANSWER_SCHEMA
    )
    result = asyncio.run(provider().generate(request))
    return result.text, ground_answer(parse_model_output(result.text), blocks)


def test_the_real_model_answers_in_english_with_a_valid_citation() -> None:
    _, grounded = ask(
        "When are Aman rice seedlings transplanted?",
        "Farmers transplant Aman rice seedlings in July, when monsoon rain has filled the fields.",
    )

    assert grounded.answerable and grounded.labels == ["S1"] and "July" in grounded.text  # type: ignore[attr-defined]


def test_the_real_model_answers_a_bangla_question_from_bangla_text() -> None:
    _, grounded = ask(
        "কোন মাসে ইলিশ ধরা নিষিদ্ধ থাকে?",
        "ডিম ছাড়ার মৌসুমে অক্টোবর মাসে প্রায় বাইশ দিন ইলিশ ধরা নিষিদ্ধ থাকে।",
    )

    assert grounded.answerable and "অক্টোবর" in grounded.text  # type: ignore[attr-defined]


def test_the_real_model_abstains_when_the_sources_do_not_answer() -> None:
    _, grounded = ask(
        "What is the capital of France?", "Farmers transplant Aman rice seedlings in July."
    )

    assert not grounded.answerable  # type: ignore[attr-defined]


def test_an_unreachable_server_is_reported_not_hidden() -> None:
    dead = OllamaProvider(
        LLMConfig("ollama", "http://127.0.0.1:9", MODEL, 0.1, 50, 4096, 5, "none")
    )

    with pytest.raises(LLMUnavailableError):
        asyncio.run(dead.generate(GenerationRequest("s", "u", 10)))
