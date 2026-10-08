import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory
from tests.files import make_user, text_bytes, upload_ok
from tests.helpers import DEFAULT_PASSWORD, bearer, create_user_in_db, db_execute, login, tokens
from tests.pdf_factory import make_pdf
from tests.processing_helpers import fetch_document, process_now, row_of

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"


def _failed_document(client: TestClient, headers: dict[str, str], db: str) -> str:
    doc_id = str(
        upload_ok(
            client, headers, filename="x.txt", content=text_bytes("failed doc"), content_type=None
        )["id"]
    )
    db_execute(
        db,
        "UPDATE documents SET status='failed', failure_reason='corrupt_document', "
        "error_message='The document appears to be damaged and could not be read.', "
        "processing_attempts=1 WHERE id = :d",
        d=doc_id,
    )
    return doc_id


def test_new_uploads_report_pending_with_no_processing_data(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    doc = upload_ok(
        auth_client, alice, filename="a.txt", content=text_bytes("pending"), content_type=None
    )

    assert doc["status"] == "pending"
    assert doc["page_count"] is None and doc["character_count"] is None
    assert doc["processing_started_at"] is None and doc["failure_reason"] is None


def test_failed_documents_expose_only_a_code_and_a_safe_message(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _failed_document(auth_client, alice, migrated_database_url)

    shown = fetch_document(auth_client, alice, doc_id)

    assert shown["failure_reason"] == "corrupt_document"
    assert shown["error_message"].startswith("The document appears")
    assert "Traceback" not in str(shown) and "/" not in shown["error_message"]


def test_retry_requeues_a_failed_document_with_a_fresh_attempt_budget(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _failed_document(auth_client, alice, migrated_database_url)

    response = auth_client.post(f"{DOCS}/{doc_id}/retry", headers=alice)

    assert response.status_code == 202
    data = response.json()["data"]
    assert (
        data["status"] == "pending"
        and data["failure_reason"] is None
        and data["error_message"] is None
    )
    assert row_of(migrated_database_url, doc_id)["processing_attempts"] == 0


@pytest.mark.parametrize("state", ["pending", "parsing", "ready"])
def test_only_failed_documents_can_be_retried(
    auth_client: TestClient, migrated_database_url: str, state: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = str(
        upload_ok(
            auth_client, alice, filename="x.txt", content=text_bytes(state), content_type=None
        )["id"]
    )
    db_execute(
        migrated_database_url, "UPDATE documents SET status = :s WHERE id = :d", s=state, d=doc_id
    )

    response = auth_client.post(f"{DOCS}/{doc_id}/retry", headers=alice)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert row_of(migrated_database_url, doc_id)["status"] == state


def test_retry_requires_authentication(auth_client: TestClient) -> None:
    assert auth_client.post(f"{DOCS}/{uuid.uuid4()}/retry").status_code == 401


def test_retry_cannot_touch_another_users_document(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    doc_id = _failed_document(auth_client, alice, migrated_database_url)

    foreign = auth_client.post(f"{DOCS}/{doc_id}/retry", headers=bob)
    absent = auth_client.post(f"{DOCS}/{uuid.uuid4()}/retry", headers=bob)

    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["error"] == absent.json()["error"]
    assert row_of(migrated_database_url, doc_id)["status"] == "failed"


def test_admin_can_retry_any_document_per_the_documented_policy(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _failed_document(auth_client, alice, migrated_database_url)
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(
        tokens(login(auth_client, email="admin@example.com", password=DEFAULT_PASSWORD))[
            "access_token"
        ]
    )

    assert auth_client.post(f"{DOCS}/{doc_id}/retry", headers=admin).status_code == 202


def test_retry_is_rate_limited_per_user(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(rate_limit_upload_attempts=2)
    alice = make_user(client, "alice@example.com")
    doc_id = _failed_document(client, alice, migrated_database_url)

    statuses = [client.post(f"{DOCS}/{doc_id}/retry", headers=alice).status_code for _ in range(4)]

    assert statuses[2:] == [429, 429]


def test_deleting_a_processed_document_removes_its_sections(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_scalar

    alice = make_user(auth_client, "alice@example.com")
    doc_id = str(
        upload_ok(
            auth_client,
            alice,
            filename="a.pdf",
            content=make_pdf(["x y z", "p q"]),
            content_type=None,
        )["id"]
    )
    process_now(auth_client, doc_id)
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM document_sections") == 2

    assert auth_client.delete(f"{DOCS}/{doc_id}", headers=alice).status_code == 204

    assert db_scalar(migrated_database_url, "SELECT count(*) FROM document_sections") == 0


def test_processing_does_not_cross_user_boundaries_in_listing(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    doc_id = str(
        upload_ok(
            auth_client, alice, filename="a.pdf", content=make_pdf(["mine text"]), content_type=None
        )["id"]
    )
    process_now(auth_client, doc_id)

    assert auth_client.get(DOCS, headers=bob).json()["data"] == []
    assert auth_client.get(f"{DOCS}?status=ready", headers=alice).json()["meta"]["total_items"] == 1
