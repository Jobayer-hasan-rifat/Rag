import asyncio
import json
import logging
import uuid
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.embeddings.registry import register_provider
from app.embeddings.types import EmbeddingFailedError, Vectors
from tests.conftest import ClientFactory
from tests.embedding_fakes import HashProvider
from tests.files import create_collection, make_user
from tests.helpers import DEFAULT_PASSWORD, bearer, create_user_in_db, db_execute, login, tokens
from tests.search_helpers import ready_doc

pytestmark = pytest.mark.integration

HYBRID = "/api/v1/search/hybrid"
SEMANTIC = "/api/v1/search/semantic"
DOCS = "/api/v1/documents"


def hybrid(client: TestClient, headers: dict[str, str] | None, **body: Any) -> Any:
    return client.post(HYBRID, headers=headers or {}, json=body)


def names(response: Any) -> list[str]:
    return [item["document_name"] for item in response.json()["data"]["results"]]


# --- the contract ---------------------------------------------------------------------------


def test_hybrid_results_carry_ranks_scores_and_source_metadata(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Rice farmers plant seedlings during the monsoon", "rice.txt")
    ready_doc(auth_client, alice, "Database indexes speed up queries", "db.txt")

    response = hybrid(auth_client, alice, query="database indexes")

    assert response.status_code == 200
    data = response.json()["data"]
    top = data["results"][0]
    assert top["document_name"] == "db.txt" and top["rank"] == 1
    assert top["dense_rank"] == 1 and top["sparse_rank"] == 1
    assert top["score"] == pytest.approx(2 / 61, rel=1e-6)  # rank 1 in both lists, k = 60
    assert -1 <= top["dense_score"] <= 1
    assert [r["rank"] for r in data["results"]] == list(range(1, len(data["results"]) + 1))


def test_the_response_exposes_exactly_the_documented_fields(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "A short passage about rivers", "r.txt")

    body = hybrid(auth_client, alice, query="rivers").json()

    assert set(body["data"]) == {"results", "search"}
    assert set(body["data"]["results"][0]) == {
        "rank", "chunk_id", "document_id", "document_name", "chunk_index", "section_ordinal",
        "page_number", "heading", "heading_level", "heading_path", "text", "start_char",
        "end_char", "score", "dense_rank", "sparse_rank", "dense_score",
    }  # fmt: skip
    assert set(body["data"]["search"]) == {
        "mode", "fusion", "rrf_k", "top_k", "returned", "dense_candidates", "sparse_candidates",
        "overlap", "retrievers_used", "degraded", "embedding_model", "model_revision",
        "searched_documents", "latency_ms",
    }  # fmt: skip
    info = body["data"]["search"]
    assert (info["mode"], info["fusion"], info["rrf_k"]) == ("hybrid", "rrf", 60)
    assert info["retrievers_used"] == ["dense", "sparse"] and info["degraded"] == []
    assert info["overlap"] == 1 and info["returned"] == 1
    assert {"request_id", "timestamp"} <= set(body["meta"])


def test_a_result_found_by_only_one_retriever_has_a_null_rank_for_the_other(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "unrelated words only here", "a.txt")

    results = hybrid(auth_client, alice, query="zzzz").json()["data"]["results"]

    assert results and all(r["sparse_rank"] is None for r in results)  # nothing lexical matches
    assert all(r["dense_rank"] is not None and r["dense_score"] is not None for r in results)


def test_an_exact_term_is_found_even_when_nothing_is_semantically_close(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    for i in range(6):
        ready_doc(auth_client, alice, f"general notes about meetings and plans {i}", f"n{i}.txt")
    target = ready_doc(auth_client, alice, "see ticket KESTREL-9 for the rollback steps", "t.txt")

    response = hybrid(auth_client, alice, query="KESTREL-9")

    top = response.json()["data"]["results"][0]
    assert top["document_id"] == target and top["sparse_rank"] == 1


def test_bangla_queries_work_through_both_retrievers(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "ইলিশ বাংলাদেশের জাতীয় মাছ এবং নদীতে পাওয়া যায়", "bn.txt")
    ready_doc(auth_client, alice, "সুন্দরবন বিশ্বের বৃহত্তম ম্যানগ্রোভ বন", "bn2.txt")
    ready_doc(auth_client, alice, "PostgreSQL ডাটাবেজে index যোগ করলে query দ্রুত হয়", "mx.txt")

    bangla = hybrid(auth_client, alice, query="ইলিশ জাতীয় মাছ").json()["data"]["results"]
    mixed = hybrid(auth_client, alice, query="query দ্রুত index").json()["data"]["results"]

    assert bangla[0]["document_name"] == "bn.txt" and bangla[0]["sparse_rank"] == 1
    assert mixed[0]["document_name"] == "mx.txt"


def test_phrase_queries_boost_adjacent_matches(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(
        auth_client, alice, "fish is national in a way; the Bangladesh national mood", "apart.txt"
    )
    adjacent = ready_doc(
        auth_client, alice, "the national fish of Bangladesh is the hilsa", "adj.txt"
    )

    results = hybrid(auth_client, alice, query='"national fish"').json()["data"]["results"]

    lexical = [r for r in results if r["sparse_rank"] is not None]
    assert [r["document_id"] for r in lexical] == [adjacent]  # only the adjacent chunk matches


# --- compatibility ---------------------------------------------------------------------------


def test_the_semantic_endpoint_is_unchanged(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "exact phrase to find", "a.txt")

    response = auth_client.post(SEMANTIC, headers=alice, json={"query": "exact phrase to find"})

    result = response.json()["data"]["results"][0]
    assert set(result) >= {"score", "distance", "rank"} and result["score"] > 0.99
    assert set(response.json()["data"]["search"]) >= {"metric", "score_type"}
    assert "sparse_rank" not in result  # no hybrid fields leaked into the semantic contract


def test_the_hybrid_query_is_embedded_exactly_like_a_semantic_query(
    auth_client_factory: ClientFactory,
) -> None:
    seen: list[str] = []

    class Recording(HashProvider):
        def embed_query(self, text: str) -> Vectors:
            seen.append(text)
            return super().embed_query(text)

    register_provider("test_recording", lambda cfg, rt: Recording(cfg))
    client = auth_client_factory(embedding_provider="test_recording", embedding_query_prefix="q: ")
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "some content", "a.txt")

    client.post(SEMANTIC, headers=alice, json={"query": "  Same   Query ইলিশ "})
    hybrid(client, alice, query="  Same   Query ইলিশ ")

    assert (
        len(seen) == 2 and seen[0] == seen[1] == "Same Query ইলিশ"
    )  # the provider adds the prefix


# --- limits and validation -------------------------------------------------------------------


def test_top_k_defaults_and_is_bounded(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(search_default_top_k=3, search_max_top_k=5)
    alice = make_user(client, "alice@example.com")
    for i in range(7):
        ready_doc(client, alice, f"shared words document number {i} {'x' * i}", f"d{i}.txt")

    assert len(hybrid(client, alice, query="shared words").json()["data"]["results"]) == 3
    assert len(hybrid(client, alice, query="shared words", top_k=5).json()["data"]["results"]) == 5
    assert hybrid(client, alice, query="shared words", top_k=6).status_code == 422
    assert hybrid(client, alice, query="shared words", top_k=0).status_code == 422


def test_candidate_limits_bound_what_each_retriever_contributes(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(hybrid_dense_candidates=2, hybrid_sparse_candidates=3)
    alice = make_user(client, "alice@example.com")
    for i in range(8):
        ready_doc(client, alice, f"candidate keyword number {i}", f"d{i}.txt")

    info = hybrid(client, alice, query="candidate keyword", top_k=20).json()["data"]["search"]

    assert info["dense_candidates"] == 2 and info["sparse_candidates"] == 3
    assert info["returned"] <= 5  # at most the union of the two candidate lists


@pytest.mark.parametrize(
    "body",
    [{}, {"query": ""}, {"query": "   "}, {"query": "x" * 10_001}, {"query": 5},
     {"query": "ok", "document_ids": ["nope"]}, {"query": "ok", "collection_id": "nope"},
     {"query": "ok", "top_k": "many"}],
    ids=["missing", "empty", "blank", "huge", "not-text", "bad-id", "bad-coll", "bad-top-k"],
)  # fmt: skip
def test_malformed_requests_are_rejected(auth_client: TestClient, body: dict[str, Any]) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = auth_client.post(HYBRID, headers=alice, json=body)

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_over_long_queries_are_rejected_by_the_configured_limit(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(search_max_query_chars=40)
    alice = make_user(client, "alice@example.com")

    assert hybrid(client, alice, query="y" * 40).status_code == 200
    assert hybrid(client, alice, query="y" * 41).status_code == 422


@pytest.mark.parametrize("query", ["?!...", "--- *** ---", '""', "( ) [ ]", "‍"])
def test_punctuation_only_queries_still_answer_through_the_semantic_path(
    auth_client: TestClient, query: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "some content", "a.txt")

    response = hybrid(auth_client, alice, query=query)

    assert response.status_code in (200, 422)
    if response.status_code == 200:
        info = response.json()["data"]["search"]
        assert info["sparse_candidates"] == 0 and info["degraded"] == []


@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE document_chunks; --",
        '" OR 1=1 --',
        "a' || (SELECT pg_sleep(5)) || '",
        "$$ x $$",
    ],
)
def test_hostile_text_is_just_a_query(auth_client: TestClient, query: str) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "intact content", "a.txt")

    response = hybrid(auth_client, alice, query=query)

    assert response.status_code == 200
    assert names(hybrid(auth_client, alice, query="intact")) == ["a.txt"]  # tables still there


def test_oversized_bodies_are_refused(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    big = json.dumps({"query": "x", "pad": "y" * 200_000})

    refused = auth_client.post(
        HYBRID, headers={**alice, "Content-Type": "application/json"}, content=big
    )

    assert refused.status_code == 413 and refused.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_hybrid_search_shares_the_per_user_search_rate_limit(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rate_limit_search_attempts=3)
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")

    codes = [
        hybrid(client, alice, query="x").status_code,
        client.post(SEMANTIC, headers=alice, json={"query": "x"}).status_code,
        hybrid(client, alice, query="x").status_code,
        hybrid(client, alice, query="x").status_code,
    ]

    assert codes == [200, 200, 200, 429]
    assert hybrid(client, bob, query="x").status_code == 200


def test_a_get_is_not_a_search(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    assert auth_client.get(HYBRID + "?query=hello", headers=alice).status_code == 405


# --- authentication and isolation -----------------------------------------------------------


def test_hybrid_search_requires_authentication(auth_client: TestClient) -> None:
    assert hybrid(auth_client, None, query="x").status_code == 401
    assert hybrid(auth_client, bearer("junk"), query="x").status_code == 401


def test_a_user_never_sees_another_users_content_through_either_retriever(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "alice confidential quarterly zebra numbers", "alice.txt")
    ready_doc(auth_client, bob, "bob public gardening notes", "bob.txt")

    # an exact term (lexical hit) and a paraphrase (dense hit) of Alice's text
    for query in ("zebra", "alice confidential quarterly zebra numbers", "quarterly numbers"):
        response = hybrid(auth_client, bob, query=query)
        assert names(response) == ["bob.txt"], query
        assert (
            "alice" not in response.text.replace("alice.txt", "") and "zebra" not in response.text
        )
        info = response.json()["data"]["search"]
        assert info["sparse_candidates"] == 0 and info["overlap"] == 0


def test_candidate_counts_do_not_reveal_other_users_data(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "shared vocabulary appears here", "a.txt")
    before = hybrid(auth_client, alice, query="shared vocabulary").json()["data"]
    for i in range(10):
        ready_doc(auth_client, bob, f"shared vocabulary shared vocabulary {i}", f"b{i}.txt")

    after = hybrid(auth_client, alice, query="shared vocabulary").json()["data"]

    for key in ("dense_candidates", "sparse_candidates", "overlap", "returned"):
        assert after["search"][key] == before["search"][key], key
    assert [r["score"] for r in after["results"]] == [r["score"] for r in before["results"]]


def test_filters_naming_foreign_documents_or_collections_are_a_404_like_a_missing_one(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    foreign = ready_doc(auth_client, alice, "alice only", "a.txt")
    collection = create_collection(auth_client, alice, "Private")
    own = ready_doc(auth_client, bob, "bob only", "b.txt")

    stolen = hybrid(auth_client, bob, query="alice only", document_ids=[foreign])
    absent = hybrid(auth_client, bob, query="alice only", document_ids=[str(uuid.uuid4())])
    mixed = hybrid(auth_client, bob, query="x", document_ids=[own, foreign])
    stolen_collection = hybrid(auth_client, bob, query="x", collection_id=collection)
    missing_collection = hybrid(auth_client, bob, query="x", collection_id=str(uuid.uuid4()))

    assert stolen.status_code == absent.status_code == mixed.status_code == 404
    assert stolen.json()["error"] == absent.json()["error"] == mixed.json()["error"]
    assert stolen_collection.status_code == missing_collection.status_code == 404
    assert stolen_collection.json()["error"] == missing_collection.json()["error"]


def test_administrators_search_only_their_own_documents(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    foreign = ready_doc(auth_client, alice, "alice private text", "a.txt")
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(
        tokens(login(auth_client, email="admin@example.com", password=DEFAULT_PASSWORD))[
            "access_token"
        ]
    )

    assert hybrid(auth_client, admin, query="alice private text").json()["data"]["results"] == []
    assert hybrid(auth_client, admin, query="x", document_ids=[foreign]).status_code == 404


def test_document_and_collection_filters_restrict_both_retrievers(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    collection = create_collection(auth_client, alice, "Papers")
    inside = ready_doc(
        auth_client, alice, "shared topic inside", "in.txt", collection_ids=[collection]
    )
    outside = ready_doc(auth_client, alice, "shared topic outside", "out.txt")

    only_inside = hybrid(auth_client, alice, query="shared topic", collection_id=collection)
    only_outside = hybrid(auth_client, alice, query="shared topic", document_ids=[outside])

    assert [r["document_id"] for r in only_inside.json()["data"]["results"]] == [inside]
    assert [r["document_id"] for r in only_outside.json()["data"]["results"]] == [outside]


# --- status changes and deletion -----------------------------------------------------------


def test_documents_leaving_the_ready_state_stop_appearing(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "topic that comes and goes", "a.txt")
    assert names(hybrid(auth_client, alice, query="topic that comes and goes")) == ["a.txt"]

    for status in ("embedding", "chunked", "failed"):
        db_execute(
            migrated_database_url,
            "UPDATE documents SET status = :s, failure_reason = NULL, "
            "error_message = NULL WHERE id = :d",
            s=status,
            d=doc,
        )
        response = hybrid(auth_client, alice, query="topic that comes and goes")
        assert response.json()["data"]["results"] == [], status
        info = response.json()["data"]["search"]
        assert info["dense_candidates"] == 0 and info["sparse_candidates"] == 0
    db_execute(migrated_database_url, "UPDATE documents SET status = 'ready' WHERE id = :d", d=doc)
    assert names(hybrid(auth_client, alice, query="topic that comes and goes")) == ["a.txt"]


def test_deleted_documents_disappear_from_hybrid_results(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "temporary content", "t.txt")
    assert hybrid(auth_client, alice, query="temporary").json()["data"]["results"]

    assert auth_client.delete(f"{DOCS}/{doc}", headers=alice).status_code == 204

    assert hybrid(auth_client, alice, query="temporary").json()["data"]["results"] == []


def test_a_chunk_that_lost_its_ready_status_between_steps_is_never_shown(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.repositories.lexical_repository import LexicalRepository

    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "race condition content", "a.txt")
    original = LexicalRepository.candidates

    async def candidates_then_change_status(self, **kwargs):  # type: ignore[no-untyped-def]
        found = await original(self, **kwargs)
        await asyncio.to_thread(
            db_execute,
            migrated_database_url,
            "UPDATE documents SET status = 'failed' WHERE id = :d",
            d=doc,
        )
        return found

    monkeypatch.setattr(LexicalRepository, "candidates", candidates_then_change_status)

    response = hybrid(auth_client, alice, query="race condition content")

    assert response.status_code == 200
    assert response.json()["data"]["results"] == []  # hydration re-checks status and ownership


# --- partial failures ------------------------------------------------------------------------


class _QueryFails(HashProvider):
    def embed_query(self, text: str) -> Vectors:
        raise EmbeddingFailedError("simulated")


def test_if_the_model_is_unavailable_the_lexical_retriever_still_answers(
    auth_client_factory: ClientFactory,
) -> None:
    register_provider("test_hybrid_down", lambda cfg, rt: _QueryFails(cfg))
    client = auth_client_factory(embedding_provider="test_hybrid_down")
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "kestrel nine rollback steps", "a.txt")

    response = hybrid(client, alice, query="kestrel rollback")

    assert response.status_code == 200
    info = response.json()["data"]["search"]
    assert info["degraded"] == ["dense"] and info["retrievers_used"] == ["sparse"]
    result = response.json()["data"]["results"][0]
    assert result["document_name"] == "a.txt" and result["dense_rank"] is None
    assert result["sparse_rank"] == 1 and result["dense_score"] is None


def test_if_the_lexical_query_fails_the_dense_retriever_still_answers(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.db.repositories.lexical_repository import LexicalRepository

    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "semantic content here", "a.txt")

    async def broken(self, **kwargs):  # type: ignore[no-untyped-def]
        raise OperationalError("SELECT 1", {}, Exception("database went away"))

    monkeypatch.setattr(LexicalRepository, "candidates", broken)

    response = hybrid(auth_client, alice, query="semantic content")

    assert response.status_code == 200
    info = response.json()["data"]["search"]
    assert info["degraded"] == ["sparse"] and info["retrievers_used"] == ["dense"]
    assert "database went away" not in response.text
    assert response.json()["data"]["results"][0]["sparse_rank"] is None


def test_if_both_retrievers_fail_the_request_fails_without_details(
    auth_client_factory: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.db.repositories.lexical_repository import LexicalRepository

    register_provider("test_hybrid_both", lambda cfg, rt: _QueryFails(cfg))
    client = auth_client_factory(embedding_provider="test_hybrid_both")
    alice = make_user(client, "alice@example.com")

    async def broken(self, **kwargs):  # type: ignore[no-untyped-def]
        raise OperationalError("SELECT secret", {}, Exception("password=hunter2"))

    monkeypatch.setattr(LexicalRepository, "candidates", broken)

    response = hybrid(client, alice, query="anything")

    assert response.status_code == 503
    assert "hunter2" not in response.text and "secret" not in response.text


def test_if_only_the_model_fails_and_no_lexical_match_exists_the_answer_is_empty_not_an_error(
    auth_client_factory: ClientFactory,
) -> None:
    register_provider("test_hybrid_empty", partial(_QueryFails))
    client = auth_client_factory(embedding_provider="test_hybrid_empty")
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "plain words", "a.txt")

    response = hybrid(client, alice, query="nomatchhere")

    assert response.status_code == 200 and response.json()["data"]["results"] == []
    assert response.json()["data"]["search"]["degraded"] == ["dense"]


# --- logging ---------------------------------------------------------------------------------


def test_hybrid_search_logs_metrics_but_never_the_query_or_the_text(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "MARKER-DOCUMENT-TEXT lives here", "m.txt")

    with caplog.at_level(logging.DEBUG):
        hybrid(auth_client, alice, query="MARKER-QUERY-TEXT lives")

    assert "MARKER-QUERY-TEXT" not in caplog.text and "MARKER-DOCUMENT-TEXT" not in caplog.text
    event = next(r for r in caplog.records if r.getMessage() == "hybrid search")
    for field in ("dense_candidates", "sparse_candidates", "overlap", "dense_ms", "sparse_ms"):
        assert hasattr(event, field), field
    assert event.returned == 1 and event.query_chars > 0  # type: ignore[attr-defined]


def test_the_openapi_description_documents_the_endpoint(auth_client: TestClient) -> None:
    operation = auth_client.get("/openapi.json").json()["paths"][HYBRID]["post"]

    assert "Reciprocal Rank Fusion" in operation["description"]
    assert {"401", "404", "422", "429", "503"} <= set(operation["responses"])


def test_function_words_alone_do_not_feed_the_lexical_retriever(
    auth_client_factory: ClientFactory,
) -> None:
    kept = auth_client_factory()
    alice = make_user(kept, "alice@example.com")
    ready_doc(kept, alice, "the boat is in the harbour", "a.txt")
    filtered = hybrid(kept, alice, query="what is the").json()["data"]["search"]

    raw_client = auth_client_factory(sparse_remove_stopwords=False)
    bob = make_user(raw_client, "bob@example.com")
    ready_doc(raw_client, bob, "the boat is in the harbour", "b.txt")
    raw = hybrid(raw_client, bob, query="what is the").json()["data"]["search"]

    assert filtered["sparse_candidates"] == 0
    assert raw["sparse_candidates"] == 1  # the switch restores plain matching


def test_a_zero_weight_retriever_adds_no_candidates_to_the_fusion(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(hybrid_sparse_weight=0.0)
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "kestrel rollback steps", "a.txt")
    ready_doc(client, alice, "unrelated gardening notes", "b.txt")

    results = hybrid(client, alice, query="kestrel").json()["data"]["results"]

    assert results and all(r["sparse_rank"] is None for r in results)
    assert all(r["score"] > 0 for r in results)


def test_the_lexical_statement_timeout_setting_is_applied_without_breaking_search(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(sparse_statement_timeout_ms=100)
    alice = make_user(client, "alice@example.com")
    ready_doc(client, alice, "plain searchable text", "a.txt")

    info = hybrid(client, alice, query="searchable").json()["data"]["search"]

    assert info["sparse_candidates"] == 1 and info["degraded"] == []
