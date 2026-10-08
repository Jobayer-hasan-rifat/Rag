import uuid

import pytest
from fastapi.testclient import TestClient

from tests.files import make_user, upload_ok
from tests.helpers import DEFAULT_PASSWORD, bearer, create_user_in_db, login, tokens
from tests.pdf_factory import make_pdf
from tests.processing_helpers import process_now

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"
BANGLA = "বাংলাদেশের রাজধানী ঢাকা। এটি একটি পরীক্ষামূলক বাক্য। "


def _chunked_document(client: TestClient, headers: dict[str, str], name: str = "n.txt") -> str:
    body = "\n\n".join(f"Paragraph {i}. " + "Lorem ipsum dolor sit amet. " * 12 for i in range(12))
    doc_id = str(
        upload_ok(client, headers, filename=name, content=body.encode(), content_type=None)["id"]
    )
    assert process_now(client, doc_id).status == "chunked"
    return doc_id


def test_owner_can_inspect_chunks_in_reading_order(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)

    response = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice)

    assert response.status_code == 200
    body = response.json()
    items = body["data"]
    assert [c["chunk_index"] for c in items] == sorted(c["chunk_index"] for c in items)
    assert items[0]["chunk_index"] == 0 and items[0]["text"].startswith("Paragraph 0.")
    assert body["meta"]["total_items"] >= len(items) > 0
    assert {"page", "page_size", "total_pages", "request_id", "timestamp"} <= set(body["meta"])


def test_chunk_response_exposes_exactly_the_documented_fields(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)

    chunk = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice).json()["data"][0]

    assert set(chunk) == {
        "id", "chunk_index", "text", "char_count", "section_ordinal", "page_number", "heading",
        "heading_level", "heading_path", "start_char", "end_char", "overlap_chars",
    }  # fmt: skip
    assert chunk["char_count"] == len(chunk["text"])
    assert chunk["end_char"] - chunk["start_char"] == chunk["char_count"]


def test_chunk_listing_is_paginated(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)
    total = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice).json()["meta"]["total_items"]
    assert total > 2

    first = auth_client.get(f"{DOCS}/{doc_id}/chunks?page=1&page_size=2", headers=alice).json()
    second = auth_client.get(f"{DOCS}/{doc_id}/chunks?page=2&page_size=2", headers=alice).json()

    assert [c["chunk_index"] for c in first["data"]] == [0, 1]
    assert [c["chunk_index"] for c in second["data"]] == [2, 3][: len(second["data"])]
    assert first["meta"]["total_items"] == total and first["meta"]["page_size"] == 2


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=1000", "page=abc"])
def test_invalid_pagination_is_rejected(auth_client: TestClient, query: str) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)

    response = auth_client.get(f"{DOCS}/{doc_id}/chunks?{query}", headers=alice)

    assert response.status_code in {400, 422}


def test_pdf_chunks_report_page_numbers_and_bangla_text(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    pdf = make_pdf([BANGLA * 5, "English page content that is long enough to chunk."])
    doc_id = str(
        upload_ok(auth_client, alice, filename="m.pdf", content=pdf, content_type=None)["id"]
    )
    process_now(auth_client, doc_id)

    items = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice).json()["data"]

    assert {c["page_number"] for c in items} == {1, 2}
    assert "ঢাকা" in items[0]["text"]


def test_document_without_chunks_returns_an_empty_page(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = str(
        upload_ok(auth_client, alice, filename="p.txt", content=b"pending", content_type=None)["id"]
    )

    response = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice)

    assert response.status_code == 200
    assert response.json()["data"] == [] and response.json()["meta"]["total_items"] == 0


def test_chunks_require_authentication(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)

    assert auth_client.get(f"{DOCS}/{doc_id}/chunks").status_code == 401
    assert auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=bearer("junk")).status_code == 401


def test_other_users_cannot_read_chunks_and_cannot_tell_the_document_exists(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    doc_id = _chunked_document(auth_client, alice)

    foreign = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=bob)
    absent = auth_client.get(f"{DOCS}/{uuid.uuid4()}/chunks", headers=bob)

    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["error"] == absent.json()["error"]
    assert "Paragraph" not in foreign.text


def test_admin_can_inspect_chunks_per_the_documented_policy(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(
        tokens(login(auth_client, email="admin@example.com", password=DEFAULT_PASSWORD))[
            "access_token"
        ]
    )

    assert auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=admin).status_code == 200


def test_chunk_endpoint_rejects_malformed_ids(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    assert auth_client.get(f"{DOCS}/not-a-uuid/chunks", headers=alice).status_code in {400, 422}


def test_chunks_are_removed_when_the_document_is_deleted(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_scalar

    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)
    assert auth_client.delete(f"{DOCS}/{doc_id}", headers=alice).status_code == 204

    assert auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice).status_code == 404
    count = db_scalar(
        migrated_database_url,
        "SELECT count(*) FROM document_chunks WHERE document_id = :d",
        d=doc_id,
    )
    assert count == 0


def test_document_detail_and_list_report_chunk_counts(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked_document(auth_client, alice)
    total = auth_client.get(f"{DOCS}/{doc_id}/chunks", headers=alice).json()["meta"]["total_items"]

    detail = auth_client.get(f"{DOCS}/{doc_id}", headers=alice).json()["data"]
    listed = auth_client.get(DOCS, headers=alice).json()["data"]

    assert detail["chunk_count"] == total and detail["status"] == "chunked"
    assert next(d for d in listed if d["id"] == doc_id)["chunk_count"] == total
