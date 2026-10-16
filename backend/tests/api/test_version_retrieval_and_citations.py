import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.reranking.registry import get_reranker
from tests.api.test_conversations_api import history_of, new_id, say
from tests.conftest import ClientFactory
from tests.files import make_user
from tests.helpers import db_execute, db_rows
from tests.search_helpers import ready_doc
from tests.version_helpers import DOCS, ready_version, search_names

pytestmark = pytest.mark.integration

V1 = (
    "Rice manual edition one: seedlings are transplanted in July after the monsoon rain. "
    "Ticket ERR-4721 explains the old rule."
)
V2 = "Rice manual edition two: seedlings are transplanted in August after the monsoon rain."
QUESTION = "When are rice seedlings transplanted according to the manual?"


@pytest.fixture
def client(auth_client_factory: ClientFactory) -> TestClient:
    return auth_client_factory(rag_enabled=True, rerank_enabled=True)


@pytest.fixture
def alice(client: TestClient) -> dict[str, str]:
    return make_user(client, "alice@example.com")


@pytest.fixture
def doc(client: TestClient, alice: dict[str, str]) -> str:
    return ready_doc(client, alice, V1, "manual-1.txt")


def post(client: TestClient, path: str, headers: dict[str, str], **body: Any) -> Any:
    return client.post(f"/api/v1/search/{path}", headers=headers, json=body)


def citations_of(client: TestClient, headers: dict[str, str], conversation: str) -> list[Any]:
    messages = history_of(client, headers, conversation).json()["data"]["messages"]
    return [c for m in messages for c in m["citations"]]


# --- every retrieval path uses only the active version -----------------------------------------


def test_semantic_hybrid_lexical_and_reranked_results_exclude_the_superseded_version(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    ready_version(client, alice, doc, V2, "manual-2.txt")

    for path, extra in (
        ("semantic", {}),
        ("hybrid", {}),
        ("hybrid", {"rerank": True}),
        ("hybrid", {"document_ids": [doc]}),
    ):
        response = post(client, path, alice, query="rice seedlings transplanted", top_k=10, **extra)
        assert response.status_code == 200, (path, extra, response.text)
        results = response.json()["data"]["results"]
        assert results, (path, extra)
        assert all("edition two" in r["text"] for r in results), (path, extra)
        assert all(r["document_id"] == doc and r["version_number"] == 2 for r in results)
        assert all(r["document_name"] == "manual-2.txt" for r in results)  # one version's data


def test_an_identifier_found_only_in_the_old_version_is_not_found_by_keyword_search(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    assert any("ERR-4721" in t for t in search_names(client, alice, "ERR-4721"))
    ready_version(client, alice, doc, V2)

    # the dense retriever still returns neighbours; none of them is the superseded text
    hybrid = search_names(client, alice, "ERR-4721")
    semantic = [
        r["text"]
        for r in post(client, "semantic", alice, query="ERR-4721", top_k=5).json()["data"][
            "results"
        ]
    ]
    assert all("ERR-4721" not in t for t in hybrid + semantic)
    only_sparse = post(client, "hybrid", alice, query='"ERR-4721"', top_k=5).json()["data"]
    assert all(r["sparse_rank"] is None for r in only_sparse["results"])  # no keyword match left


def test_restoring_the_old_version_makes_only_its_text_searchable_again(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    ready_version(client, alice, doc, V2)

    assert client.post(f"{DOCS}/{doc}/versions/{doc}/activate", headers=alice).status_code == 200

    texts = search_names(client, alice, "rice seedlings transplanted")
    assert texts and all("edition one" in t for t in texts)
    assert search_names(client, alice, "ERR-4721")


def test_a_retained_version_being_reindexed_does_not_affect_the_active_one(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    ready_version(client, alice, doc, V2)
    # the retained version is re-indexed (for example after a model change) and is not ready
    db_execute(
        migrated_database_url, "UPDATE documents SET status = 'embedding' WHERE id = :i", i=doc
    )

    texts = search_names(client, alice, "rice seedlings transplanted")

    assert texts and all("edition two" in t for t in texts)  # the other version is unaffected


def test_another_users_search_never_sees_either_version(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    ready_version(client, alice, doc, V2)
    bob = make_user(client, "bob@example.com")
    ready_doc(client, bob, "Bob writes about fish farming", "fish.txt")

    texts = search_names(client, bob, "rice seedlings transplanted")

    assert all("edition" not in t for t in texts)
    forged = post(client, "hybrid", bob, query="rice", document_ids=[doc])
    assert forged.status_code == 404


# --- answers and their citations --------------------------------------------------------------


def test_an_answer_cites_the_exact_version_it_was_read_from(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    before = client.post("/api/v1/rag/answer", headers=alice, json={"question": QUESTION})
    v2 = ready_version(client, alice, doc, V2, "manual-2.txt")
    after = client.post("/api/v1/rag/answer", headers=alice, json={"question": QUESTION})

    old = before.json()["data"]["citations"][0]
    new = after.json()["data"]["citations"][0]
    assert old["document_id"] == doc and old["version_id"] == doc and old["version_number"] == 1
    assert new["document_id"] == doc and new["version_id"] == v2 and new["version_number"] == 2
    assert "edition one" in old["excerpt"] and "edition two" in new["excerpt"]


def test_a_stored_citation_keeps_resolving_to_the_version_it_was_grounded_in(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    conversation = new_id(client, alice)
    assert say(client, alice, conversation, QUESTION).status_code == 201
    v2 = ready_version(client, alice, doc, V2, "manual-2.txt")

    cited = citations_of(client, alice, conversation)

    assert len(cited) == 1
    assert cited[0]["version_id"] == doc and cited[0]["version_number"] == 1
    assert cited[0]["superseded"] is True and cited[0]["source_available"] is True
    assert "edition one" in cited[0]["excerpt"] and "edition two" not in cited[0]["excerpt"]
    assert cited[0]["document_name"] == "manual-1.txt"
    again = say(client, alice, conversation, QUESTION + " (second ask)")
    fresh = again.json()["data"]["assistant_message"]["citations"][0]
    assert fresh["version_id"] == v2 and fresh["superseded"] is False
    # the old answer was not rewritten
    assert citations_of(client, alice, conversation)[0]["version_id"] == doc


def test_deleting_a_superseded_version_leaves_only_identifiers_in_stored_citations(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)
    v2 = ready_version(client, alice, doc, V2)
    say(client, alice, conversation, QUESTION + " again")

    assert client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice).status_code == 204

    first, second = citations_of(client, alice, conversation)
    assert first["removed"] is True and first["source_available"] is False
    assert first["excerpt"] is None and first["document_name"] is None
    assert first["version_id"] == doc and first["document_id"] == doc  # identifiers only
    assert second["removed"] is False and second["version_id"] == v2  # untouched
    assert second["excerpt"] and second["document_name"]
    stored = db_rows(
        migrated_database_url,
        "SELECT citations::text FROM messages WHERE role = 'assistant' ORDER BY sequence",
    )
    assert "manual-1" not in stored[0][0] and "edition one" not in stored[0][0]


def test_deleting_the_document_scrubs_every_version_of_it(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)
    ready_version(client, alice, doc, V2)
    say(client, alice, conversation, QUESTION + " again")

    assert client.delete(f"{DOCS}/{doc}", headers=alice).status_code == 204

    cited = citations_of(client, alice, conversation)
    assert len(cited) == 2 and all(c["removed"] and c["excerpt"] is None for c in cited)
    assert all(c["document_name"] is None for c in cited)


def test_deleting_a_version_does_not_scrub_citations_of_its_sibling_versions(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    # the first version's id equals the document id, so a careless match would hit them all
    ready_version(client, alice, doc, V2)
    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)  # cites the active version 2
    v3 = ready_version(client, alice, doc, V2 + " Appendix for the third edition.")
    assert client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice).status_code == 204

    cited = citations_of(client, alice, conversation)

    assert cited[0]["removed"] is False and cited[0]["version_number"] == 2
    assert cited[0]["excerpt"] and v3 != cited[0]["version_id"]


def test_a_citation_from_before_versions_existed_still_resolves(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)
    db_execute(  # what Phase 10 stored: no version fields
        migrated_database_url,
        "UPDATE messages SET citations = (SELECT jsonb_agg(e - 'version_id' - 'version_number') "
        "FROM jsonb_array_elements(citations) e) WHERE role = 'assistant'",
    )
    ready_version(client, alice, doc, V2)

    cited = citations_of(client, alice, conversation)[0]

    assert cited["version_id"] is None and cited["source_available"] is True
    assert "edition one" in cited["excerpt"] and cited["superseded"] is True


def test_a_citation_whose_chunk_belongs_to_another_version_shows_no_text(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)
    v2 = ready_version(client, alice, doc, V2)
    forged = json.dumps(v2)
    db_execute(
        migrated_database_url,
        "UPDATE messages SET citations = (SELECT jsonb_agg("
        "jsonb_set(e, '{version_id}', CAST(:v AS jsonb))) "
        "FROM jsonb_array_elements(citations) e) WHERE role = 'assistant'",
        v=forged,
    )

    cited = citations_of(client, alice, conversation)[0]

    assert cited["source_available"] is False and cited["excerpt"] is None


def test_another_user_cannot_read_the_citations_of_a_conversation(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)
    bob = make_user(client, "bob@example.com")

    assert history_of(client, bob, conversation).status_code == 404


def test_the_reranker_only_ever_sees_eligible_candidates(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    ready_version(client, alice, doc, V2)

    response = post(client, "hybrid", alice, query="rice seedlings", rerank=True, top_k=5)

    assert response.status_code == 200
    reranker = get_reranker(client.app.state.settings)  # type: ignore[attr-defined]
    seen = [text for _query, passages in reranker.calls for text in passages]  # type: ignore[attr-defined]
    assert seen and all("edition one" not in text for text in seen)


def test_reprocessing_a_version_is_not_a_new_version_and_old_citations_lose_only_their_text(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    from tests.processing_helpers import embed_now, rechunk_now

    conversation = new_id(client, alice)
    say(client, alice, conversation, QUESTION)
    before = citations_of(client, alice, conversation)[0]

    assert rechunk_now(client, doc).status == "chunked"
    assert embed_now(client, doc).status == "ready"

    shown = client.get(f"{DOCS}/{doc}", headers=alice).json()["data"]
    assert shown["version_count"] == 1 and shown["version_id"] == doc  # the same version
    assert shown["active_version_id"] == doc and shown["searchable"] is True
    after = citations_of(client, alice, conversation)[0]
    # new chunk ids: the stored reference can no longer be resolved, and nothing else is shown
    assert after["chunk_id"] == before["chunk_id"] and after["version_id"] == doc
    assert after["source_available"] is False and after["excerpt"] is None
