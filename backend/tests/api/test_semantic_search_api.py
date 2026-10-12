import json
import logging
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.embeddings.registry import register_provider
from app.embeddings.types import EmbeddingConfig, EmbeddingFailedError, Vectors
from tests.conftest import ClientFactory
from tests.embedding_fakes import ControlledProvider, HashProvider
from tests.files import create_collection, make_user, upload_ok
from tests.helpers import DEFAULT_PASSWORD, bearer, create_user_in_db, db_execute, login, tokens
from tests.pdf_factory import make_pdf
from tests.processing_helpers import embed_now, process_now

pytestmark = pytest.mark.integration

SEARCH = "/api/v1/search/semantic"
DOCS = "/api/v1/documents"


def ready_doc(
    client: TestClient,
    headers: dict[str, str],
    text: str | bytes,
    name: str = "d.txt",
    collection_ids: list[str] | None = None,
) -> str:
    data = text.encode() if isinstance(text, str) else text
    uploaded = upload_ok(
        client,
        headers,
        filename=name,
        content=data,
        content_type=None,
        collection_ids=collection_ids,
    )
    doc_id = str(uploaded["id"])
    assert process_now(client, doc_id).status == "chunked"
    assert embed_now(client, doc_id).status == "ready"
    return doc_id


def search(client: TestClient, headers: dict[str, str] | None, **body: Any) -> Any:
    return client.post(SEARCH, headers=headers or {}, json=body)


def texts(response: Any) -> list[str]:
    return [item["text"] for item in response.json()["data"]["results"]]


# --- ranking and scores ------------------------------------------------------------------


def test_results_are_ranked_by_similarity_with_scores_and_source_metadata(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "Rice farmers plant seedlings during the monsoon", "rice.txt")
    ready_doc(auth_client, alice, "Database indexes speed up queries", "db.txt")
    ready_doc(auth_client, alice, "Dengue spreads through mosquito bites", "dengue.txt")

    response = search(auth_client, alice, query="how do database indexes speed queries")

    assert response.status_code == 200
    results = response.json()["data"]["results"]
    assert [r["rank"] for r in results] == [1, 2, 3]
    assert results[0]["document_name"] == "db.txt" and "indexes" in results[0]["text"]
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[1]
    assert all(abs(r["score"] + r["distance"] - 1.0) < 1e-5 for r in results)


def test_search_response_exposes_exactly_the_documented_fields(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "A short passage about rivers", "r.txt")

    body = search(auth_client, alice, query="rivers").json()

    assert set(body["data"]) == {"results", "search"}
    assert set(body["data"]["results"][0]) == {
        "rank", "chunk_id", "document_id", "document_name", "chunk_index", "section_ordinal",
        "page_number", "heading", "heading_level", "heading_path", "text", "start_char",
        "end_char", "score", "distance",
    }  # fmt: skip
    info = body["data"]["search"]
    assert set(info) == {
        "embedding_model", "model_revision", "metric", "score_type", "top_k", "returned",
        "searched_documents", "latency_ms",
    }  # fmt: skip
    assert info["embedding_model"] == "test-hash" and info["model_revision"] == "test-revision-1"
    assert info["metric"] == "cosine" and info["score_type"] == "cosine_similarity"
    assert info["returned"] == 1 and isinstance(info["latency_ms"], int)
    assert {"request_id", "timestamp"} <= set(body["meta"])


def test_identical_text_scores_near_one_and_min_score_filters(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "exact phrase to find", "a.txt")
    ready_doc(auth_client, alice, "completely unrelated words here", "b.txt")

    everything = search(auth_client, alice, query="exact phrase to find").json()["data"]["results"]
    strict = search(auth_client, alice, query="exact phrase to find", min_score=0.9)

    assert everything[0]["score"] > 0.99 and everything[1]["score"] < 0.5
    assert [r["document_name"] for r in strict.json()["data"]["results"]] == ["a.txt"]


def test_controlled_vectors_give_the_exact_expected_order_and_scores(
    auth_client_factory: ClientFactory,
) -> None:
    table = {
        "chunk aligned": [1.0, 0.0],
        "chunk close": [0.8, 0.6],
        "chunk orthogonal": [0.0, 1.0],
        "chunk opposite": [-1.0, 0.0],
        "the query": [1.0, 0.0],
    }
    register_provider("test_controlled", lambda cfg, rt: ControlledProvider(cfg, table))
    client = auth_client_factory(embedding_provider="test_controlled")
    alice = make_user(client, "alice@example.com")
    for name in ("chunk opposite", "chunk orthogonal", "chunk close", "chunk aligned"):
        ready_doc(client, alice, name, f"{name}.txt")

    results = search(client, alice, query="the query").json()["data"]["results"]

    assert [r["text"] for r in results] == [
        "chunk aligned", "chunk close", "chunk orthogonal", "chunk opposite",
    ]  # fmt: skip
    assert [round(r["score"], 3) for r in results] == [1.0, 0.8, 0.0, -1.0]
    assert [round(r["distance"], 3) for r in results] == [0.0, 0.2, 1.0, 2.0]


def test_pdf_pages_and_markdown_headings_are_reported_as_source_metadata(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(
        auth_client, alice, make_pdf(["First page about rivers", "Second page about rice"]), "p.pdf"
    )
    ready_doc(
        auth_client,
        alice,
        b"# Guide\n\nIntro text for the guide.\n\n## Setup\n\nInstall the tool carefully.\n",
        "g.md",
    )

    pages = search(auth_client, alice, query="second page about rice").json()["data"]["results"]
    setup = search(auth_client, alice, query="install the tool carefully").json()["data"]["results"]

    assert pages[0]["page_number"] == 2 and pages[0]["section_ordinal"] == 1
    assert setup[0]["heading"] == "Setup" and setup[0]["heading_path"] == ["Guide", "Setup"]
    assert setup[0]["end_char"] > setup[0]["start_char"]


# --- limits and validation ---------------------------------------------------------------


def test_top_k_defaults_and_is_bounded(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(search_default_top_k=3, search_max_top_k=5)
    alice = make_user(client, "alice@example.com")
    for i in range(7):
        ready_doc(client, alice, f"shared words document number {i} {'x' * i}", f"d{i}.txt")

    assert len(search(client, alice, query="shared words").json()["data"]["results"]) == 3
    assert len(search(client, alice, query="shared words", top_k=5).json()["data"]["results"]) == 5
    assert len(search(client, alice, query="shared words", top_k=1).json()["data"]["results"]) == 1
    assert search(client, alice, query="shared words", top_k=6).status_code == 422
    assert search(client, alice, query="shared words", top_k=201).status_code == 422


@pytest.mark.parametrize("top_k", [0, -1, "many", 1.5, None])
def test_invalid_top_k_is_rejected(auth_client: TestClient, top_k: Any) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = search(auth_client, alice, query="anything", top_k=top_k)

    assert response.status_code == (200 if top_k is None else 422)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "   \n\t "},
        {"query": "x" * 10_001},
        {"query": 123},
        {"query": "ok", "document_ids": ["not-a-uuid"]},
        {"query": "ok", "document_ids": [str(uuid.uuid4())] * 101},
        {"query": "ok", "min_score": 2},
        {"query": "ok", "collection_id": "nope"},
    ],
    ids=[
        "missing",
        "empty",
        "whitespace",
        "huge",
        "not-text",
        "bad-id",
        "too-many-ids",
        "bad-score",
        "bad-collection",
    ],
)
def test_malformed_requests_are_rejected_with_a_validation_error(
    auth_client: TestClient, body: dict[str, Any]
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = auth_client.post(SEARCH, headers=alice, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_queries_over_the_configured_length_are_rejected(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(search_max_query_chars=50)
    alice = make_user(client, "alice@example.com")

    assert search(client, alice, query="y" * 50).status_code == 200
    long = search(client, alice, query="y" * 51)
    assert long.status_code == 422 and "50" in long.json()["error"]["message"]


def test_a_non_json_or_get_request_is_not_a_search(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    assert auth_client.get(SEARCH + "?query=hello", headers=alice).status_code == 405
    assert auth_client.post(SEARCH, headers=alice, content=b"query=hello").status_code == 422


# --- authentication, ownership, isolation ---------------------------------------------


def test_search_requires_authentication(auth_client: TestClient) -> None:
    assert search(auth_client, None, query="x").status_code == 401
    assert search(auth_client, bearer("junk"), query="x").status_code == 401


def test_a_user_never_finds_another_users_content(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "alice secret quarterly numbers zebra", "alice.txt")
    ready_doc(auth_client, bob, "bob public notes about gardens", "bob.txt")

    bob_view = search(auth_client, bob, query="alice secret quarterly numbers zebra")
    alice_view = search(auth_client, alice, query="alice secret quarterly numbers zebra")

    assert [r["document_name"] for r in bob_view.json()["data"]["results"]] == ["bob.txt"]
    assert "zebra" not in bob_view.text and "alice" not in bob_view.text.replace("alice.txt", "")
    assert alice_view.json()["data"]["results"][0]["document_name"] == "alice.txt"


def test_a_user_with_no_searchable_documents_gets_an_empty_result(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "content that exists", "a.txt")

    response = search(auth_client, bob, query="content that exists")

    assert response.status_code == 200
    assert response.json()["data"]["results"] == []
    assert response.json()["data"]["search"]["returned"] == 0


def test_filtering_by_another_users_document_looks_exactly_like_a_missing_document(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    foreign = ready_doc(auth_client, alice, "alice only", "a.txt")
    own = ready_doc(auth_client, bob, "bob only", "b.txt")

    stolen = search(auth_client, bob, query="alice only", document_ids=[foreign])
    absent = search(auth_client, bob, query="alice only", document_ids=[str(uuid.uuid4())])
    mixed = search(auth_client, bob, query="alice only", document_ids=[own, foreign])

    assert stolen.status_code == absent.status_code == mixed.status_code == 404
    assert stolen.json()["error"] == absent.json()["error"] == mixed.json()["error"]
    assert "alice" not in stolen.text


def test_filtering_by_another_users_collection_is_a_404(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    collection = create_collection(auth_client, alice, "Private")
    ready_doc(auth_client, alice, "inside the collection", "a.txt", collection_ids=[collection])

    stolen = search(auth_client, bob, query="inside the collection", collection_id=collection)
    absent = search(auth_client, bob, query="x", collection_id=str(uuid.uuid4()))

    assert stolen.status_code == absent.status_code == 404
    assert stolen.json()["error"] == absent.json()["error"]


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

    assert search(auth_client, admin, query="alice private text").json()["data"]["results"] == []
    assert search(auth_client, admin, query="x", document_ids=[foreign]).status_code == 404


def test_database_blocks_a_vector_that_claims_the_wrong_owner(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    ready_doc(auth_client, alice, "alice text", "a.txt")
    bob_id = auth_client.get("/api/v1/auth/me", headers=bob).json()["data"]["id"]

    with pytest.raises(Exception, match="fk_chunk_embeddings_document_owner"):
        db_execute(
            migrated_database_url,
            "UPDATE chunk_embeddings SET user_id = :u",
            u=bob_id,
        )


# --- filters ----------------------------------------------------------------------------


def test_document_and_collection_filters_restrict_the_search(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    collection = create_collection(auth_client, alice, "Papers")
    inside = ready_doc(
        auth_client, alice, "shared topic inside", "in.txt", collection_ids=[collection]
    )
    outside = ready_doc(auth_client, alice, "shared topic outside", "out.txt")

    both = search(auth_client, alice, query="shared topic")
    only_inside = search(auth_client, alice, query="shared topic", collection_id=collection)
    only_outside = search(auth_client, alice, query="shared topic", document_ids=[outside])

    assert {r["document_name"] for r in both.json()["data"]["results"]} == {"in.txt", "out.txt"}
    assert [r["document_id"] for r in only_inside.json()["data"]["results"]] == [inside]
    assert [r["document_id"] for r in only_outside.json()["data"]["results"]] == [outside]


def test_only_ready_documents_are_searched(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready = ready_doc(auth_client, alice, "topic that is ready", "ready.txt")
    chunked = str(
        upload_ok(
            auth_client, alice, filename="c.txt", content=b"topic still chunked", content_type=None
        )["id"]
    )
    process_now(auth_client, chunked)
    partial_doc = ready_doc(auth_client, alice, "topic half way", "partial.txt")
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status = 'embedding' WHERE id = :d",
        d=partial_doc,
    )

    results = search(auth_client, alice, query="topic").json()["data"]["results"]

    assert [r["document_id"] for r in results] == [ready]


def test_documents_embedded_under_another_configuration_are_not_searched(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "content under the old model", "old.txt")
    new_settings = auth_client.app.state.settings.model_copy(  # type: ignore[attr-defined]
        update={"embedding_model_revision": "test-revision-2"}
    )
    auth_client.app.state.settings = new_settings  # type: ignore[attr-defined]

    response = search(auth_client, alice, query="content under the old model")

    assert response.status_code == 200 and response.json()["data"]["results"] == []


def test_deleted_documents_disappear_from_search(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = ready_doc(auth_client, alice, "temporary content", "t.txt")
    assert search(auth_client, alice, query="temporary content").json()["data"]["results"]

    assert auth_client.delete(f"{DOCS}/{doc}", headers=alice).status_code == 204

    assert search(auth_client, alice, query="temporary content").json()["data"]["results"] == []


# --- multilingual -----------------------------------------------------------------------


def test_bangla_and_mixed_language_text_is_searchable(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "ইলিশ বাংলাদেশের জাতীয় মাছ এবং নদীতে পাওয়া যায়", "bn.txt")
    ready_doc(auth_client, alice, "সুন্দরবন বিশ্বের বৃহত্তম ম্যানগ্রোভ বন", "bn2.txt")
    ready_doc(auth_client, alice, "PostgreSQL ডাটাবেজে index যোগ করলে query দ্রুত হয়", "mx.txt")

    bangla = search(auth_client, alice, query="ইলিশ জাতীয় মাছ").json()["data"]["results"]
    mixed = search(auth_client, alice, query="query দ্রুত হয় index").json()["data"]["results"]

    assert bangla[0]["document_name"] == "bn.txt" and "ইলিশ" in bangla[0]["text"]
    assert mixed[0]["document_name"] == "mx.txt"


def test_queries_are_unicode_normalised_like_documents(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "a quiet café by the river", "cafe.txt")
    decomposed = unicodedata.normalize("NFD", "café")
    assert decomposed != "café"

    composed_hit = search(auth_client, alice, query="quiet café river").json()["data"]["results"][0]
    decomposed_hit = search(auth_client, alice, query=f"quiet {decomposed} river").json()["data"][
        "results"
    ][0]

    assert decomposed_hit["document_name"] == "cafe.txt"
    assert decomposed_hit["score"] == pytest.approx(composed_hit["score"], abs=1e-6)
    assert decomposed_hit["score"] > 0.5


@pytest.mark.parametrize(
    "query", ["ইলিশ… কবে?!", "“quoted” (query) — with / punctuation", "😀 emoji only 🎉", "‍‌"]
)
def test_punctuation_and_unusual_unicode_do_not_break_search(
    auth_client: TestClient, query: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "some searchable content", "a.txt")

    response = search(auth_client, alice, query=query)

    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json()["data"]["search"]["returned"] <= 10


# --- operational behaviour --------------------------------------------------------------


def test_search_is_rate_limited_per_user(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(rate_limit_search_attempts=2)
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")

    assert [search(client, alice, query="x").status_code for _ in range(3)] == [200, 200, 429]
    assert search(client, bob, query="x").status_code == 200
    limited = search(client, alice, query="x")
    assert limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(limited.headers["Retry-After"]) >= 1


class _QueryProvider(HashProvider):
    def __init__(
        self,
        config: EmbeddingConfig,
        runtime: Any = None,
        *,
        query_error: Callable[[], Exception] | None = None,
        delay: float = 0.0,
    ) -> None:
        super().__init__(config)
        self._query_error = query_error
        self._delay = delay

    def embed_query(self, text: str) -> Vectors:
        time.sleep(self._delay)
        if self._query_error:
            raise self._query_error()
        return super().embed_query(text)


def test_an_unavailable_model_gives_a_503_without_internal_details(
    auth_client_factory: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    register_provider(
        "test_query_down",
        partial(
            _QueryProvider, query_error=lambda: EmbeddingFailedError("boom /srv/models secret")
        ),
    )
    client = auth_client_factory(embedding_provider="test_query_down")
    alice = make_user(client, "alice@example.com")

    response = search(client, alice, query="anything")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMBEDDING_UNAVAILABLE"
    assert (
        "boom" not in response.text
        and "/srv" not in response.text
        and "Traceback" not in response.text
    )


def test_a_slow_model_times_out_and_saturation_is_reported_as_busy(
    auth_client_factory: ClientFactory,
) -> None:
    register_provider("test_query_slow", partial(_QueryProvider, delay=2.5))
    client = auth_client_factory(
        embedding_provider="test_query_slow",
        search_query_timeout_seconds=1,
        search_max_concurrent_queries=1,
    )
    alice = make_user(client, "alice@example.com")

    first = search(client, alice, query="slow")  # times out after 1 s; the thread keeps running
    second = search(client, alice, query="slow")  # the slot is still held by that thread

    assert first.status_code == 503 and second.status_code == 503
    assert "busy" in second.json()["error"]["message"].lower()
    time.sleep(1.2)  # the first thread has finished by now and released its slot
    third = search(client, alice, query="slow")  # slow again, but it gets the slot
    assert third.status_code == 503 and "busy" not in third.json()["error"]["message"].lower()


def test_concurrent_searches_all_succeed_within_the_concurrency_limit(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "parallel query target", "p.txt")
    statuses: list[int] = []

    def run() -> None:
        statuses.append(search(auth_client, alice, query="parallel query target").status_code)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert statuses == [200] * 8


def test_search_logs_metrics_but_never_the_query_or_the_text(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    ready_doc(auth_client, alice, "MARKER-DOCUMENT-TEXT lives here", "m.txt")

    with caplog.at_level(logging.DEBUG):
        search(auth_client, alice, query="MARKER-QUERY-TEXT lives")

    assert "MARKER-QUERY-TEXT" not in caplog.text and "MARKER-DOCUMENT-TEXT" not in caplog.text
    event = next(r for r in caplog.records if r.getMessage() == "semantic search")
    assert event.returned == 1 and event.top_k == 10 and event.query_chars > 0  # type: ignore[attr-defined]
    assert hasattr(event, "latency_ms") and hasattr(event, "embed_ms")


def test_search_openapi_documents_the_endpoint(auth_client: TestClient) -> None:
    schema = auth_client.get("/openapi.json").json()

    operation = schema["paths"][SEARCH]["post"]
    assert "cosine" in operation["description"] and {"401", "404", "422", "429", "503"} <= set(
        operation["responses"]
    )


def test_oversized_search_bodies_are_refused_before_they_are_buffered(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    big = json.dumps({"query": "x" * 10_000, "padding": "y" * 200_000})

    refused = auth_client.post(
        SEARCH, headers={**alice, "Content-Type": "application/json"}, content=big
    )
    fine = auth_client.post(SEARCH, headers=alice, json={"query": "ক" * 10_000})

    assert refused.status_code == 413
    assert refused.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert refused.json()["error"]["details"]["max_bytes"] == 128 * 1024
    assert fine.status_code in (200, 422)  # the largest legitimate body still gets through


def test_the_body_limit_applies_only_to_search_not_to_other_json_endpoints(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    big = {"name": "n" * 200_000}

    response = auth_client.post("/api/v1/collections", headers=alice, json=big)

    assert response.status_code == 422  # rejected by validation, not by the body cap
