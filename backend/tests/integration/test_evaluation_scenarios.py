"""The scenario dataset as hard gates: pipeline guarantees that must hold in every change.

The deterministic profile (hashing embeddings, scripted generation model) needs no model download
and no ML package. A failed `hard` check is a correctness or isolation failure, never a quality
regression; `behaviour` checks are exact here because the scripted model is faithful.
"""

import pytest
from fastapi.testclient import TestClient

from evaluation.scenario_runner import ScenarioRunner, load_scenarios, summarise
from evaluation.stack import Stack
from tests.conftest import ClientFactory

pytestmark = pytest.mark.integration

SCENARIOS = load_scenarios()["scenarios"]
RUNNABLE = [s["id"] for s in SCENARIOS if "deterministic" in s.get("profiles", ["deterministic"])]


@pytest.fixture
def stack(auth_client_factory: ClientFactory, migrated_database_url: str) -> Stack:
    client: TestClient = auth_client_factory(rag_enabled=True, rerank_enabled=True)
    return Stack.attach(client, migrated_database_url)


@pytest.mark.parametrize("scenario_id", RUNNABLE)
def test_scenario_holds(stack: Stack, scenario_id: str) -> None:
    outcome = ScenarioRunner(stack).run_all({scenario_id})[0]

    assert outcome.error is None, outcome.error
    failed = [(c.name, c.klass, c.detail) for c in outcome.checks if not c.passed]
    assert failed == []
    assert outcome.checks, "a scenario that checks nothing proves nothing"


def test_summary_counts_every_check(stack: Stack) -> None:
    outcomes = ScenarioRunner(stack).run_all({RUNNABLE[0]})

    summary = summarise(outcomes)

    assert summary["scenarios_run"] == 1 and summary["hard_failed"] == 0
    assert summary["hard_checks"] >= 1
