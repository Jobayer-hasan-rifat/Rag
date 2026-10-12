import asyncio
import uuid
from typing import Any

import pytest

from app.reranking.ranking import RerankInput
from app.reranking.registry import get_reranker
from app.services.rerank_service import RerankOutcome, RerankService
from tests.conftest import SettingsFactory
from tests.rerank_fakes import FakeReranker


def inputs(*texts: str) -> list[RerankInput[uuid.UUID]]:
    return [RerankInput(uuid.UUID(int=i + 1), t, i + 1) for i, t in enumerate(texts)]


def run(
    settings: Any,
    candidates: list[RerankInput[uuid.UUID]],
    query: str = "alpha beta",
    top_k: int = 5,
) -> tuple[RerankOutcome, FakeReranker, asyncio.Semaphore]:
    reranker = get_reranker(settings)
    assert isinstance(reranker, FakeReranker)

    async def go() -> tuple[RerankOutcome, asyncio.Semaphore]:
        gate = asyncio.Semaphore(settings.rerank_max_concurrent)
        service = RerankService(gate, settings)
        return await service.rerank(query, candidates, top_k=top_k), gate

    outcome, gate = asyncio.run(go())
    return outcome, reranker, gate


def test_candidates_are_reordered_by_the_raw_score(make_settings: SettingsFactory) -> None:
    settings = make_settings(rerank_enabled=True)

    outcome, reranker, _ = run(settings, inputs("nothing here", "alpha only", "alpha beta both"))

    assert outcome.applied and outcome.fallback_reason is None
    assert [r.retrieval_rank for r in outcome.items] == [3, 2, 1]
    assert [r.score for r in outcome.items] == [2.0, 1.0, 0.0]
    assert outcome.candidates == 3 and reranker.loads == 1


def test_top_k_limits_the_result_but_all_candidates_are_scored(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(rerank_enabled=True)

    outcome, reranker, _ = run(settings, inputs("a", "b", "alpha", "c"), top_k=1)

    assert [r.retrieval_rank for r in outcome.items] == [3]
    assert len(reranker.calls[0][1]) == 4


def test_duplicate_candidates_are_scored_once(make_settings: SettingsFactory) -> None:
    settings = make_settings(rerank_enabled=True)
    one = RerankInput(uuid.UUID(int=1), "alpha", 1)
    dup = RerankInput(uuid.UUID(int=1), "alpha", 5)

    outcome, reranker, _ = run(settings, [one, dup, *inputs("x")[:0]])

    assert [r.retrieval_rank for r in outcome.items] == [1]
    assert reranker.calls[0][1] == ["alpha"]


def test_no_candidates_means_nothing_to_do_and_no_model_load(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(rerank_enabled=True)

    outcome, reranker, _ = run(settings, [])

    assert outcome.applied and outcome.items == [] and reranker.loads == 0 and not reranker.calls


def test_truncation_flags_are_passed_through(make_settings: SettingsFactory) -> None:
    settings = make_settings(rerank_enabled=True)

    outcome, _, _ = run(settings, inputs("alpha " * 100, "alpha beta"), query="alpha")

    assert outcome.truncated == 1
    assert {r.retrieval_rank: r.truncated for r in outcome.items} == {1: True, 2: False}


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        ("test_unavailable", "unavailable"),
        ("test_failing", "failed"),
        ("test_nan", "invalid"),
        ("test_short", "invalid"),
    ],
)
def test_failures_become_a_reported_fallback_not_scores(
    make_settings: SettingsFactory, provider: str, reason: str
) -> None:
    settings = make_settings(rerank_enabled=True, rerank_provider=provider)

    outcome, _, gate = run(settings, inputs("alpha", "beta"))

    assert not outcome.applied and outcome.fallback_reason == reason
    assert outcome.items == []  # never a fabricated or default score
    assert gate._value == settings.rerank_max_concurrent  # the slot was released


def test_a_passed_deadline_is_a_timeout_fallback(make_settings: SettingsFactory) -> None:
    settings = make_settings(rerank_enabled=True, rerank_provider="test_deadline")

    outcome, _, _ = run(settings, inputs("alpha"))

    assert not outcome.applied and outcome.fallback_reason == "timeout"


def test_a_blocked_model_is_abandoned_after_the_total_timeout(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(
        rerank_enabled=True, rerank_provider="test_slow", rerank_timeout_seconds=1
    ).model_copy(update={"rerank_load_timeout_seconds": 0})

    outcome, _, _ = run(settings, inputs("alpha"))

    assert not outcome.applied and outcome.fallback_reason == "timeout"


def test_when_every_slot_is_taken_the_request_falls_back_as_busy(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(rerank_enabled=True).model_copy(update={"rerank_timeout_seconds": 0})
    reranker = get_reranker(settings)

    async def go() -> RerankOutcome:
        gate = asyncio.Semaphore(1)
        await gate.acquire()  # someone else is scoring
        return await RerankService(gate, settings).rerank("alpha", inputs("alpha"), top_k=1)

    outcome = asyncio.run(go())

    assert not outcome.applied and outcome.fallback_reason == "busy"
    assert not reranker.calls  # type: ignore[attr-defined]


def test_the_service_never_touches_a_model_it_was_not_given_candidates_for(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(rerank_enabled=True)

    _, reranker, _ = run(settings, [])

    assert reranker.device == "unloaded"


def test_a_reranker_that_breaks_its_contract_becomes_a_fallback(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(rerank_enabled=True, rerank_provider="test_crash")

    outcome, _, gate = run(settings, inputs("alpha"))

    assert not outcome.applied and outcome.fallback_reason == "failed"
    assert gate._value == settings.rerank_max_concurrent


def test_an_unknown_provider_is_reported_as_unavailable_not_raised(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(rerank_enabled=True, rerank_provider="no-such-provider")

    async def go() -> RerankOutcome:
        gate = asyncio.Semaphore(1)
        return await RerankService(gate, settings).rerank("q", inputs("alpha"), top_k=1)

    outcome = asyncio.run(go())

    assert not outcome.applied and outcome.fallback_reason == "unavailable"
