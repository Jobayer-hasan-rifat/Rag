import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.reranking.registry import get_reranker
from tests.conftest import ClientFactory
from tests.files import make_user
from tests.helpers import db_execute
from tests.rerank_fakes import FakeReranker
from tests.search_helpers import ready_doc

pytestmark = pytest.mark.integration

HYBRID = "/api/v1/search/hybrid"
SEMANTIC = "/api/v1/search/semantic"


def search(client: TestClient, headers: dict[str, str], **body: Any) -> Any:
    return client.post(HYBRID, headers=headers, json=body)


def reranker_of(client: TestClient) -> FakeReranker:
    reranker = get_reranker(client.app.state.settings)  # type: ignore[attr-defined]
    assert isinstance(reranker, FakeReranker)
    return reranker


def corpus(client: TestClient, headers: dict[str, str]) -> list[str]:
    return [
        ready_doc(client, headers, text, f"d{i}.txt")
        for i, text in enumerate(
            [
                "rice farmers plant seedlings in the monsoon season",
                "database indexes speed up queries on large tables",
                "indexes in a book help readers find a topic quickly",
                "mosquito nets protect families from dengue at night",
            ]
        )
    ]


@pytest.fixture
def rerank_client(auth_client_factory: ClientFactory) -> TestClient:
    return auth_client_factory(rerank_enabled=True)


# --- default behaviour is untouched ---------------------------------------------------------


def test_without_the_flag_the_response_and_order_are_exactly_the_hybrid_ones(
    rerank_client: TestClient,
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    corpus(rerank_client, alice)

    plain = search(rerank_client, alice, query="database indexes")
    explicit_off = search(rerank_client, alice, query="database indexes", rerank=False)

    def stable(response: Any) -> dict[str, Any]:
        data = response.json()["data"]
        data["search"].pop("latency_ms")  # the only value that differs between two calls
        return dict(data)

    assert plain.status_code == 200 and stable(plain) == stable(explicit_off)
    assert "rerank" not in plain.json()["data"]["search"]
    assert "rerank_score" not in plain.json()["data"]["results"][0]
    assert reranker_of(rerank_client).loads == 0 and not reranker_of(rerank_client).calls


def test_when_reranking_is_disabled_no_model_is_created_or_loaded(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "some content here", "a.txt")

    assert search(auth_client, alice, query="content").status_code == 200

    reranker = reranker_of(auth_client)  # created now, by the test, not by the request
    assert reranker.loads == 0 and not reranker.calls


def test_asking_for_reranking_when_it_is_disabled_is_a_clear_422(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = search(auth_client, alice, query="x", rerank=True)

    assert response.status_code == 422 and response.json()["error"]["code"] == "RERANKING_DISABLED"


def test_the_semantic_endpoint_ignores_and_never_uses_reranking(
    rerank_client: TestClient,
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    ready_doc(rerank_client, alice, "exact phrase here", "a.txt")

    response = rerank_client.post(
        SEMANTIC, headers=alice, json={"query": "exact phrase here", "rerank": True}
    )

    assert response.status_code == 200
    assert "rerank" not in response.json()["data"]["search"]
    assert reranker_of(rerank_client).loads == 0


def test_preloading_happens_only_when_enabled_and_asked_for(
    auth_client_factory: ClientFactory,
) -> None:
    with_preload = auth_client_factory(rerank_enabled=True, rerank_preload=True)
    with_preload.get("/openapi.json")
    without = auth_client_factory(rerank_enabled=True, rerank_preload=False, rerank_model="other")
    disabled = auth_client_factory(rerank_enabled=False, rerank_preload=True, rerank_model="off")

    for _ in range(50):  # the background task finishes almost immediately
        if reranker_of(with_preload).loads:
            break
        with_preload.get("/openapi.json")
    assert reranker_of(with_preload).loads == 1
    assert reranker_of(without).loads == 0 and reranker_of(disabled).loads == 0


# --- the reranked response ------------------------------------------------------------------


def test_reranked_results_carry_the_final_rank_the_raw_score_and_the_retrieval_rank(
    rerank_client: TestClient,
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    corpus(rerank_client, alice)
    baseline = search(rerank_client, alice, query="indexes speed queries", top_k=4).json()["data"]
    # make the fake prefer the candidates the other way round
    reranker_of(rerank_client).raw_scores = lambda q, ps: [float(i) for i in range(len(ps))]  # type: ignore[method-assign,assignment]

    response = search(rerank_client, alice, query="indexes speed queries", top_k=4, rerank=True)

    assert response.status_code == 200
    data = response.json()["data"]
    results = data["results"]
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    assert [r["rerank_score"] for r in results] == sorted(
        (r["rerank_score"] for r in results), reverse=True
    )
    old = {r["chunk_id"]: r for r in baseline["results"]}
    for result in results:
        before = old[result["chunk_id"]]
        assert result["retrieval_rank"] == before["rank"]  # the position before reranking
        for field in ("score", "dense_rank", "sparse_rank", "dense_score", "text"):
            assert result[field] == before[field], field  # retrieval diagnostics are preserved
    assert [r["retrieval_rank"] for r in results] == sorted(
        (r["retrieval_rank"] for r in results), reverse=True
    )  # reversed by the fake
    info = data["search"]["rerank"]
    assert info["applied"] is True and info["fallback_reason"] is None
    assert info["score_type"] == "raw_logit" and info["candidates"] == 4
    assert info["model"] == "test-fake" and data["search"]["degraded"] == []


def test_the_reranker_decides_the_order_among_the_candidates(rerank_client: TestClient) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    corpus(rerank_client, alice)

    response = search(rerank_client, alice, query="database indexes speed", top_k=2, rerank=True)

    results = response.json()["data"]["results"]
    assert len(results) == 2 and "database" in results[0]["text"]
    assert results[0]["rerank_score"] >= results[1]["rerank_score"]
    assert response.json()["data"]["search"]["top_k"] == 2
    assert response.json()["data"]["search"]["returned"] == 2


def test_candidate_limits_and_top_k_are_validated(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(rerank_enabled=True, rerank_candidates=5, rerank_max_candidates=8)
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "some words", "a.txt")

    too_many = search(client, alice, query="words", rerank=True, rerank_candidates=9)
    top_k_over = search(client, alice, query="words", rerank=True, top_k=6)
    top_k_over_custom = search(
        client, alice, query="words", rerank=True, top_k=4, rerank_candidates=3
    )
    fine = search(client, alice, query="words", rerank=True, top_k=5)
    default_clamped = search(client, alice, query="words", rerank=True)

    assert too_many.status_code == 422
    assert top_k_over.status_code == 422 and top_k_over_custom.status_code == 422
    assert fine.status_code == 200 and default_clamped.status_code == 200
    assert reranker_of(client).calls  # the valid requests did rerank


@pytest.mark.parametrize(
    "body", [{"rerank": "maybe"}, {"rerank_candidates": 0}, {"rerank_candidates": "x"}]
)
def test_malformed_rerank_options_are_rejected(
    rerank_client: TestClient, body: dict[str, Any]
) -> None:
    alice = make_user(rerank_client, "alice@example.com")

    response = search(rerank_client, alice, query="x", **body)

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_the_candidate_count_bounds_how_much_text_reaches_the_model(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rerank_enabled=True, rerank_candidates=3)
    alice = make_user(client, "alice@example.com")
    for i in range(8):
        ready_doc(client, alice, f"shared words number {i}", f"d{i}.txt")

    search(client, alice, query="shared words", rerank=True, top_k=2)

    assert len(reranker_of(client).calls[0][1]) <= 3


def test_text_that_exceeds_the_model_limit_is_flagged_not_hidden(
    rerank_client: TestClient,
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    ready_doc(rerank_client, alice, "alpha " + "filler " * 150, "long.txt")
    ready_doc(rerank_client, alice, "alpha short", "short.txt")

    response = search(rerank_client, alice, query="alpha", rerank=True)

    results = response.json()["data"]["results"]
    assert any(r["rerank_truncated"] for r in results)
    assert all(r["rerank_truncated"] == (len(r["text"]) > 400) for r in results)
    assert response.json()["data"]["search"]["rerank"]["truncated_candidates"] == sum(
        r["rerank_truncated"] for r in results
    )


# --- authorisation --------------------------------------------------------------------------


def test_only_the_callers_own_text_ever_reaches_the_reranker(rerank_client: TestClient) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    bob = make_user(rerank_client, "bob@example.com")
    ready_doc(rerank_client, alice, "alice confidential zebra budget figures", "a.txt")
    ready_doc(rerank_client, bob, "bob public gardening notes", "b.txt")

    for query in ("zebra", "alice confidential budget", "budget figures gardening"):
        response = search(rerank_client, bob, query=query, rerank=True)
        assert [r["document_name"] for r in response.json()["data"]["results"]] == ["b.txt"]
        assert "zebra" not in response.text

    seen = " ".join(" ".join(passages) for _, passages in reranker_of(rerank_client).calls)
    assert "zebra" not in seen and "confidential" not in seen


def test_a_foreign_document_or_collection_filter_is_the_same_404_as_before(
    rerank_client: TestClient,
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    bob = make_user(rerank_client, "bob@example.com")
    foreign = ready_doc(rerank_client, alice, "alice only", "a.txt")

    stolen = search(rerank_client, bob, query="x", rerank=True, document_ids=[foreign])
    absent = search(rerank_client, bob, query="x", rerank=True, document_ids=[str(uuid.uuid4())])

    assert stolen.status_code == absent.status_code == 404
    assert stolen.json()["error"] == absent.json()["error"]
    assert not reranker_of(rerank_client).calls  # nothing was scored


def test_reranking_requires_authentication(rerank_client: TestClient) -> None:
    assert rerank_client.post(HYBRID, json={"query": "x", "rerank": True}).status_code == 401


def test_documents_that_are_not_ready_or_were_deleted_are_never_reranked(
    rerank_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    gone = ready_doc(rerank_client, alice, "token alpha gone", "gone.txt")
    paused = ready_doc(rerank_client, alice, "token alpha paused", "paused.txt")
    ready_doc(rerank_client, alice, "token alpha kept", "kept.txt")
    assert rerank_client.delete(f"/api/v1/documents/{gone}", headers=alice).status_code == 204
    db_execute(
        migrated_database_url, "UPDATE documents SET status = 'failed' WHERE id = :d", d=paused
    )

    response = search(rerank_client, alice, query="token alpha", rerank=True)

    assert [r["document_name"] for r in response.json()["data"]["results"]] == ["kept.txt"]
    seen = " ".join(" ".join(p) for _, p in reranker_of(rerank_client).calls)
    assert "gone" not in seen and "paused" not in seen


def test_rerank_requests_share_the_per_user_search_rate_limit(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rerank_enabled=True, rate_limit_search_attempts=2)
    alice = make_user(client, "alice@example.com")

    codes = [search(client, alice, query="x", rerank=r).status_code for r in (True, False, True)]

    assert codes == [200, 200, 429]


# --- fallback -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        ("test_unavailable", "unavailable"),
        ("test_failing", "failed"),
        ("test_deadline", "timeout"),
        ("test_nan", "invalid"),
        ("test_short", "invalid"),
    ],
)
def test_when_reranking_cannot_run_the_hybrid_order_is_kept_and_the_response_says_so(
    auth_client_factory: ClientFactory, provider: str, reason: str
) -> None:
    client = auth_client_factory(rerank_enabled=True, rerank_provider=provider)
    alice = make_user(client, "alice@example.com")
    corpus(client, alice)
    baseline = search(client, alice, query="database indexes", top_k=3).json()["data"]["results"]

    response = search(client, alice, query="database indexes", top_k=3, rerank=True)

    assert response.status_code == 200
    data = response.json()["data"]
    info = data["search"]["rerank"]
    assert info["applied"] is False and info["fallback_reason"] == reason
    assert "rerank" in data["search"]["degraded"]
    assert [r["chunk_id"] for r in data["results"]] == [r["chunk_id"] for r in baseline]
    assert all(r["rerank_score"] is None and r["rerank_truncated"] is None for r in data["results"])
    assert [r["retrieval_rank"] for r in data["results"]] == [r["rank"] for r in data["results"]]
    assert "simulated" not in response.text and "model missing" not in response.text


def test_a_blocked_model_falls_back_without_hanging_the_request(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(
        rerank_enabled=True, rerank_provider="test_slow", rerank_timeout_seconds=1
    )
    client.app.state.settings = client.app.state.settings.model_copy(  # type: ignore[attr-defined]
        update={"rerank_load_timeout_seconds": 0}
    )
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "alpha text", "a.txt")

    response = search(client, alice, query="alpha", rerank=True)

    assert response.status_code == 200
    assert response.json()["data"]["search"]["rerank"]["fallback_reason"] == "timeout"


def test_rerank_does_not_hide_a_degraded_retriever(auth_client_factory: ClientFactory) -> None:
    from app.embeddings.registry import register_provider
    from app.embeddings.types import EmbeddingFailedError
    from tests.embedding_fakes import HashProvider

    class Down(HashProvider):
        def embed_query(self, text: str) -> Any:
            raise EmbeddingFailedError("down")

    register_provider("test_down", lambda cfg, rt: Down(cfg))
    client = auth_client_factory(rerank_enabled=True, embedding_provider="test_down")
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "kestrel rollback steps", "a.txt")

    response = search(client, alice, query="kestrel rollback", rerank=True)

    data = response.json()["data"]["search"]
    assert response.status_code == 200 and data["degraded"] == ["dense"]
    assert data["rerank"]["applied"] is True


# --- hygiene --------------------------------------------------------------------------------


def test_logs_never_contain_the_query_or_the_text(
    rerank_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    alice = make_user(rerank_client, "alice@example.com")
    ready_doc(rerank_client, alice, "MARKER-DOCUMENT-TEXT lives here", "m.txt")

    with caplog.at_level(logging.DEBUG):
        search(rerank_client, alice, query="MARKER-QUERY-TEXT lives", rerank=True)

    assert "MARKER-QUERY-TEXT" not in caplog.text and "MARKER-DOCUMENT-TEXT" not in caplog.text
    event = next(r for r in caplog.records if r.getMessage() == "reranked")
    assert event.candidates == 1 and hasattr(event, "latency_ms")  # type: ignore[attr-defined]


def test_the_openapi_document_describes_the_reranking_fields(rerank_client: TestClient) -> None:
    schemas = rerank_client.get("/openapi.json").json()["components"]["schemas"]

    assert "rerank" in schemas["HybridSearchRequest"]["properties"]
    assert {"retrieval_rank", "rerank_score"} <= set(schemas["RerankedHitResponse"]["properties"])
    assert (
        "raw model output"
        in schemas["RerankedHitResponse"]["properties"]["rerank_score"]["description"]
        or "logit" in schemas["RerankedHitResponse"]["properties"]["rerank_score"]["description"]
    )


def test_a_misconfigured_provider_never_affects_requests_that_do_not_rerank(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rerank_enabled=True, rerank_provider="no-such-provider")
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "plain words", "a.txt")

    plain = search(client, alice, query="plain")
    asked = search(client, alice, query="plain", rerank=True)

    assert plain.status_code == 200 and "rerank" not in plain.json()["data"]["search"]
    info = asked.json()["data"]["search"]["rerank"]
    assert asked.status_code == 200 and info["fallback_reason"] == "unavailable"


def test_a_crashing_model_load_at_startup_or_in_a_request_is_a_reported_fallback(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(
        rerank_enabled=True, rerank_provider="test_crash", rerank_preload=True
    )
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "plain words", "a.txt")

    response = search(client, alice, query="plain", rerank=True)

    assert response.status_code == 200
    assert response.json()["data"]["search"]["rerank"]["fallback_reason"] == "failed"
    assert "secret" not in response.text and "CUDA" not in response.text


def test_top_k_is_still_bounded_by_the_search_limit_when_reranking(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(
        rerank_enabled=True, rerank_candidates=60, rerank_max_candidates=100, search_max_top_k=50
    )
    alice = make_user(client, "alice@example.com")

    assert search(client, alice, query="x", rerank=True, top_k=51).status_code == 422
    assert search(client, alice, query="x", rerank=True, top_k=50).status_code == 200
