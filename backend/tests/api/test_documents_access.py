import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory
from tests.files import create_collection, make_user, pdf_bytes, upload, upload_ok
from tests.helpers import DEFAULT_PASSWORD, bearer, create_user_in_db, login, tokens

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"


@pytest.fixture
def world(auth_client: TestClient) -> dict[str, object]:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    doc = upload_ok(auth_client, alice, filename="secret.pdf", content=pdf_bytes("alice-secret"))
    return {"client": auth_client, "alice": alice, "bob": bob, "doc": doc["id"]}


def test_every_document_endpoint_requires_authentication(auth_client: TestClient) -> None:
    some_id = uuid.uuid4()
    calls = [
        ("get", DOCS, None),
        ("get", f"{DOCS}/{some_id}", None),
        ("get", f"{DOCS}/{some_id}/download", None),
        ("patch", f"{DOCS}/{some_id}", {"filename": "x.pdf"}),
        ("delete", f"{DOCS}/{some_id}", None),
    ]
    for method, path, body in calls:
        response = auth_client.request(method, path, json=body)
        assert response.status_code == 401, (method, path)
        assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"


def test_other_users_document_is_indistinguishable_from_a_missing_one(
    world: dict[str, object],
) -> None:
    client: TestClient = world["client"]  # type: ignore[assignment]
    bob: dict[str, str] = world["bob"]  # type: ignore[assignment]
    missing = str(uuid.uuid4())
    real = str(world["doc"])

    for method, template, body in [
        ("get", f"{DOCS}/{{id}}", None),
        ("get", f"{DOCS}/{{id}}/download", None),
        ("patch", f"{DOCS}/{{id}}", {"filename": "hijacked.pdf"}),
        ("delete", f"{DOCS}/{{id}}", None),
    ]:
        foreign = client.request(method, template.format(id=real), headers=bob, json=body)
        absent = client.request(method, template.format(id=missing), headers=bob, json=body)
        assert foreign.status_code == absent.status_code == 404, (method, template)
        assert foreign.json()["error"] == absent.json()["error"]
        assert "secret" not in foreign.text


def test_failed_cross_user_attempts_change_nothing(world: dict[str, object]) -> None:
    client: TestClient = world["client"]  # type: ignore[assignment]
    alice: dict[str, str] = world["alice"]  # type: ignore[assignment]
    bob: dict[str, str] = world["bob"]  # type: ignore[assignment]
    doc = str(world["doc"])

    client.patch(f"{DOCS}/{doc}", headers=bob, json={"filename": "hijacked.pdf"})
    client.delete(f"{DOCS}/{doc}", headers=bob)

    after = client.get(f"{DOCS}/{doc}", headers=alice).json()["data"]
    assert after["filename"] == "secret.pdf"
    assert client.get(f"{DOCS}/{doc}/download", headers=alice).content == pdf_bytes("alice-secret")


def test_listing_never_includes_other_users_documents(world: dict[str, object]) -> None:
    client: TestClient = world["client"]  # type: ignore[assignment]
    bob: dict[str, str] = world["bob"]  # type: ignore[assignment]
    upload_ok(client, bob, filename="bobs.pdf", content=pdf_bytes("bob"))

    listing = client.get(DOCS, headers=bob).json()

    assert [d["filename"] for d in listing["data"]] == ["bobs.pdf"]
    assert listing["meta"]["total_items"] == 1


def test_filtering_by_someone_elses_collection_looks_like_a_missing_collection(
    world: dict[str, object],
) -> None:
    client: TestClient = world["client"]  # type: ignore[assignment]
    alice: dict[str, str] = world["alice"]  # type: ignore[assignment]
    bob: dict[str, str] = world["bob"]  # type: ignore[assignment]
    alices = create_collection(client, alice, "Alice only")

    foreign = client.get(DOCS, headers=bob, params={"collection_id": alices})
    absent = client.get(DOCS, headers=bob, params={"collection_id": str(uuid.uuid4())})

    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["error"] == absent.json()["error"]


def test_client_supplied_owner_fields_are_ignored_or_rejected(world: dict[str, object]) -> None:
    client: TestClient = world["client"]  # type: ignore[assignment]
    alice: dict[str, str] = world["alice"]  # type: ignore[assignment]
    bob: dict[str, str] = world["bob"]  # type: ignore[assignment]
    bob_id = client.get("/api/v1/auth/me", headers=bob).json()["data"]["id"]

    smuggled_form = client.post(
        DOCS,
        headers=alice,
        files={"file": ("new.pdf", pdf_bytes("new"), "application/pdf")},
        data={"user_id": bob_id, "owner_id": bob_id},
    )
    smuggled_patch = client.patch(
        f"{DOCS}/{world['doc']}", headers=alice, json={"filename": "x.pdf", "user_id": bob_id}
    )

    assert smuggled_form.status_code == 400  # only the file and collection_ids parts are allowed
    assert smuggled_patch.status_code == 422
    assert client.get(DOCS, headers=bob).json()["data"] == []


def test_admin_can_access_any_document_by_id_per_the_documented_policy(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice, content=pdf_bytes("admin-visible"))["id"]
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(
        tokens(login(auth_client, email="admin@example.com", password=DEFAULT_PASSWORD))[
            "access_token"
        ]
    )

    meta = auth_client.get(f"{DOCS}/{doc}", headers=admin)
    download = auth_client.get(f"{DOCS}/{doc}/download", headers=admin)
    deleted = auth_client.delete(f"{DOCS}/{doc}", headers=admin)

    assert meta.status_code == 200
    assert download.content == pdf_bytes("admin-visible")
    assert deleted.status_code == 204
    assert auth_client.get(f"{DOCS}/{doc}", headers=alice).status_code == 404


def test_admin_listing_still_only_shows_the_admins_own_documents(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    upload_ok(auth_client, alice)
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(
        tokens(login(auth_client, email="admin@example.com", password=DEFAULT_PASSWORD))[
            "access_token"
        ]
    )

    assert auth_client.get(DOCS, headers=admin).json()["data"] == []


def test_admin_cannot_upload_on_behalf_of_another_user(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(
        tokens(login(auth_client, email="admin@example.com", password=DEFAULT_PASSWORD))[
            "access_token"
        ]
    )
    alices_collection = create_collection(auth_client, alice)

    response = upload(auth_client, admin, collection_ids=[alices_collection])

    assert response.status_code == 404  # collections are only attachable by their own admin/owner


def test_deactivated_user_loses_access_to_their_documents(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_execute

    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)["id"]

    db_execute(migrated_database_url, "UPDATE users SET is_active = false")

    assert auth_client.get(f"{DOCS}/{doc}", headers=alice).status_code == 401


def test_users_cannot_see_each_others_duplicates_via_the_conflict_response(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory()
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")
    upload_ok(client, alice, content=pdf_bytes("same"))

    response = upload(client, bob, content=pdf_bytes("same"))

    assert response.status_code == 201  # Bob learns nothing about Alice's identical file
