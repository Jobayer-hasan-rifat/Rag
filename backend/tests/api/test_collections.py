import uuid

import pytest
from fastapi.testclient import TestClient

from tests.files import create_collection, make_user, upload_ok

pytestmark = pytest.mark.integration

URL = "/api/v1/collections"


def test_create_collection_returns_it_with_zero_documents(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = auth_client.post(
        URL, headers=alice, json={"name": "  Research   Papers ", "description": "AI papers"}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["name"] == "Research Papers"
    assert data["description"] == "AI papers"
    assert data["document_count"] == 0
    assert "user_id" not in data


def test_collections_require_authentication(auth_client: TestClient) -> None:
    assert auth_client.get(URL).status_code == 401
    assert auth_client.post(URL, json={"name": "x"}).status_code == 401
    assert auth_client.get(f"{URL}/{uuid.uuid4()}").status_code == 401


@pytest.mark.parametrize(
    "payload", [{}, {"name": ""}, {"name": "x" * 256}, {"name": "ok", "owner": "x"}]
)
def test_invalid_collection_payloads_are_rejected(
    auth_client: TestClient, payload: dict[str, str]
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    assert auth_client.post(URL, headers=alice, json=payload).status_code == 422


def test_duplicate_names_conflict_per_user_ignoring_case(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    create_collection(auth_client, alice, "Papers")

    again = auth_client.post(URL, headers=alice, json={"name": "PAPERS"})
    other_user = auth_client.post(URL, headers=bob, json={"name": "Papers"})

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"
    assert other_user.status_code == 201  # names are only unique within one user's collections


def test_listing_only_shows_own_collections_with_pagination_and_search(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    for name in ["Beta", "alpha", "Gamma", "50%_off"]:
        create_collection(auth_client, alice, name)
    create_collection(auth_client, bob, "Bobs private")

    first = auth_client.get(URL, headers=alice, params={"page_size": 3}).json()
    second = auth_client.get(URL, headers=alice, params={"page_size": 3, "page": 2}).json()
    searched = auth_client.get(URL, headers=alice, params={"search": "AMM"}).json()
    wildcard = auth_client.get(URL, headers=alice, params={"search": "%"}).json()

    assert [c["name"] for c in first["data"]] == ["50%_off", "alpha", "Beta"]
    assert first["meta"]["total_items"] == 4 and first["meta"]["total_pages"] == 2
    assert [c["name"] for c in second["data"]] == ["Gamma"]
    assert [c["name"] for c in searched["data"]] == ["Gamma"]
    assert [c["name"] for c in wildcard["data"]] == ["50%_off"]  # LIKE wildcards are escaped
    assert "Bobs private" not in str(first) + str(second)


@pytest.mark.parametrize(
    "params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"page": "x"}]
)
def test_invalid_pagination_is_rejected(auth_client: TestClient, params: dict[str, object]) -> None:
    alice = make_user(auth_client, "alice@example.com")

    assert auth_client.get(URL, headers=alice, params=params).status_code == 422


def test_get_update_and_delete_own_collection(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    collection_id = create_collection(auth_client, alice, "Old")

    got = auth_client.get(f"{URL}/{collection_id}", headers=alice)
    updated = auth_client.patch(
        f"{URL}/{collection_id}", headers=alice, json={"name": "New", "description": "d"}
    )
    cleared = auth_client.patch(f"{URL}/{collection_id}", headers=alice, json={"description": None})
    deleted = auth_client.delete(f"{URL}/{collection_id}", headers=alice)

    assert got.status_code == 200 and got.json()["data"]["name"] == "Old"
    assert updated.json()["data"]["name"] == "New"
    assert cleared.json()["data"]["description"] is None and cleared.json()["data"]["name"] == "New"
    assert deleted.status_code == 204
    assert auth_client.get(f"{URL}/{collection_id}", headers=alice).status_code == 404


def test_update_rejects_empty_body_and_name_conflicts(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    first = create_collection(auth_client, alice, "One")
    create_collection(auth_client, alice, "Two")

    assert auth_client.patch(f"{URL}/{first}", headers=alice, json={}).status_code == 422
    assert (
        auth_client.patch(f"{URL}/{first}", headers=alice, json={"name": "two"}).status_code == 409
    )


def test_other_users_collection_looks_like_it_does_not_exist(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    private = create_collection(auth_client, alice, "Private")
    missing = str(uuid.uuid4())

    for method, path, body in [
        ("get", f"{URL}/{{id}}", None),
        ("patch", f"{URL}/{{id}}", {"name": "hijacked"}),
        ("delete", f"{URL}/{{id}}", None),
        ("post", f"{URL}/{{id}}/documents", {"document_ids": [str(uuid.uuid4())]}),
        ("delete", f"{URL}/{{id}}/documents/{uuid.uuid4()}", None),
    ]:
        real = auth_client.request(method, path.format(id=private), headers=bob, json=body)
        fake = auth_client.request(method, path.format(id=missing), headers=bob, json=body)
        assert real.status_code == fake.status_code == 404
        assert real.json()["error"] == fake.json()["error"]

    assert auth_client.get(f"{URL}/{private}", headers=alice).json()["data"]["name"] == "Private"


def test_adding_documents_is_idempotent_and_counts_them(auth_client: TestClient) -> None:
    from tests.files import pdf_bytes

    alice = make_user(auth_client, "alice@example.com")
    collection_id = create_collection(auth_client, alice)
    doc_a = upload_ok(auth_client, alice, filename="a.pdf", content=pdf_bytes("a"))["id"]
    doc_b = upload_ok(auth_client, alice, filename="b.pdf", content=pdf_bytes("b"))["id"]

    first = auth_client.post(
        f"{URL}/{collection_id}/documents",
        headers=alice,
        json={"document_ids": [doc_a, doc_b, doc_a]},
    )
    again = auth_client.post(
        f"{URL}/{collection_id}/documents", headers=alice, json={"document_ids": [doc_a]}
    )
    detail = auth_client.get(f"{URL}/{collection_id}", headers=alice).json()["data"]

    assert first.json()["data"] == {"added_count": 2, "already_exists_count": 0}
    assert again.json()["data"] == {"added_count": 0, "already_exists_count": 1}
    assert detail["document_count"] == 2


def test_cannot_add_someone_elses_documents_to_your_collection(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    alices_doc = upload_ok(auth_client, alice)["id"]
    bobs_collection = create_collection(auth_client, bob)

    response = auth_client.post(
        f"{URL}/{bobs_collection}/documents", headers=bob, json={"document_ids": [alices_doc]}
    )

    assert response.status_code == 404
    assert (
        auth_client.get(f"{URL}/{bobs_collection}", headers=bob).json()["data"]["document_count"]
        == 0
    )


def test_removing_a_document_and_deleting_a_collection_keep_the_documents(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    collection_id = create_collection(auth_client, alice)
    doc = upload_ok(auth_client, alice, collection_ids=[collection_id])["id"]

    assert (
        auth_client.delete(f"{URL}/{collection_id}/documents/{doc}", headers=alice).status_code
        == 204
    )
    assert (
        auth_client.delete(f"{URL}/{collection_id}/documents/{doc}", headers=alice).status_code
        == 404
    )
    auth_client.post(
        f"{URL}/{collection_id}/documents", headers=alice, json={"document_ids": [doc]}
    )
    assert auth_client.delete(f"{URL}/{collection_id}", headers=alice).status_code == 204

    survivor = auth_client.get(f"/api/v1/documents/{doc}", headers=alice)
    assert survivor.status_code == 200
    assert survivor.json()["data"]["collections"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"document_ids": []},
        {"document_ids": ["not-a-uuid"]},
        {},
        {"document_ids": [str(uuid.uuid4())] * 101},
    ],
)
def test_invalid_add_documents_payloads_are_rejected(
    auth_client: TestClient, body: dict[str, object]
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    collection_id = create_collection(auth_client, alice)

    assert (
        auth_client.post(f"{URL}/{collection_id}/documents", headers=alice, json=body).status_code
        == 422
    )
