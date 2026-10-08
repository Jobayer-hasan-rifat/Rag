import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory
from tests.files import (
    DOCX_TYPE,
    create_collection,
    docx_bytes,
    make_user,
    markdown_bytes,
    pdf_bytes,
    text_bytes,
    upload,
    upload_ok,
)
from tests.helpers import db_rows, db_scalar

pytestmark = pytest.mark.integration


def _stored_files(client: TestClient) -> list[Path]:
    root = Path(client.app.state.settings.storage_local_path)  # type: ignore[attr-defined]
    return [p for p in root.rglob("*") if p.is_file()]


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "file_type", "stored_type"),
    [
        ("paper.pdf", pdf_bytes(), "application/pdf", "pdf", "application/pdf"),
        ("notes.docx", docx_bytes(), DOCX_TYPE, "docx", DOCX_TYPE),
        ("readme.txt", text_bytes(), "text/plain", "txt", "text/plain; charset=utf-8"),
        ("guide.md", markdown_bytes(), "text/markdown", "md", "text/markdown; charset=utf-8"),
    ],
)
def test_each_supported_format_uploads(
    auth_client: TestClient,
    filename: str,
    content: bytes,
    content_type: str,
    file_type: str,
    stored_type: str,
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    data = upload_ok(
        auth_client, alice, filename=filename, content=content, content_type=content_type
    )

    assert data["filename"] == filename
    assert data["file_type"] == file_type
    assert data["content_type"] == stored_type
    assert data["file_size"] == len(content)
    assert data["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert data["status"] == "pending"
    assert data["error_message"] is None
    assert data["collections"] == []


def test_response_exposes_no_storage_details(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = upload(auth_client, alice)

    body = response.text
    assert "storage" not in body.lower()
    assert "documents/" not in body
    assert str(auth_client.app.state.settings.storage_local_path) not in body  # type: ignore[attr-defined]
    assert set(response.json()["data"]) == {
        "id", "filename", "file_type", "content_type", "file_size", "checksum_sha256",
        "status", "error_message", "failure_reason", "processing_started_at",
        "processing_completed_at", "page_count", "character_count",
        "collections", "created_at", "updated_at",
    }  # fmt: skip


def test_file_is_persisted_under_an_opaque_key_not_the_filename(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    content = pdf_bytes("persist")

    upload_ok(auth_client, alice, filename="Quarterly Report (final).pdf", content=content)

    files = _stored_files(auth_client)
    key = db_scalar(migrated_database_url, "SELECT storage_key FROM documents")
    assert len(files) == 1
    assert files[0].read_bytes() == content
    assert "Quarterly" not in str(files[0]) and ".pdf" not in files[0].name
    assert key.startswith("documents/") and "Quarterly" not in key
    assert files[0].as_posix().endswith(key)


def test_upload_attaches_to_own_collections(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    first = create_collection(auth_client, alice, "A")
    second = create_collection(auth_client, alice, "B")

    data = upload_ok(auth_client, alice, collection_ids=[first, second, first])

    assert [c["name"] for c in data["collections"]] == ["A", "B"]


def test_upload_to_another_users_or_unknown_collection_is_rejected_without_storing(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    import uuid

    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    bobs = create_collection(auth_client, bob)

    foreign = upload(auth_client, alice, collection_ids=[bobs])
    missing = upload(auth_client, alice, collection_ids=[str(uuid.uuid4())])

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
    assert _stored_files(auth_client) == []
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0


@pytest.mark.parametrize("raw", ["not json", "{}", '["not-a-uuid"]', "[1, 2]", '"abc"'])
def test_malformed_collection_ids_are_rejected(auth_client: TestClient, raw: str) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = auth_client.post(
        "/api/v1/documents",
        headers=alice,
        files={"file": ("a.pdf", pdf_bytes(), "application/pdf")},
        data={"collection_ids": raw},
    )

    assert response.status_code == 422
    assert _stored_files(auth_client) == []


def test_upload_requires_authentication_and_stores_nothing(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/documents", files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}
    )

    assert response.status_code == 401
    assert _stored_files(auth_client) == []


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("malware.exe", b"MZ\x90\x00", "application/x-msdownload"),
        ("page.html", b"<script>alert(1)</script>", "text/html"),
        ("image.svg", b"<svg onload=alert(1)/>", "image/svg+xml"),
        ("archive.zip", b"PK\x03\x04", "application/zip"),
        ("legacy.doc", b"\xd0\xcf\x11\xe0", "application/msword"),
        ("noextension", b"data", "application/pdf"),
        ("script.pdf.exe", pdf_bytes(), "application/pdf"),
    ],
)
def test_unsupported_types_are_rejected(
    auth_client: TestClient, filename: str, content: bytes, content_type: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = upload(
        auth_client, alice, filename=filename, content=content, content_type=content_type
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert _stored_files(auth_client) == []


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("fake.pdf", b"<html>not a pdf</html>", "application/pdf"),
        ("fake.pdf", pdf_bytes(), "text/html"),
        ("fake.docx", pdf_bytes(), DOCX_TYPE),
        ("fake.docx", b"PK\x03\x04garbage", DOCX_TYPE),
        ("fake.txt", b"\x00\x01\x02binary", "text/plain"),
        ("fake.txt", pdf_bytes(), "application/pdf"),
        ("fake.md", b"\xff\xfe\xfd", "text/markdown"),
    ],
)
def test_mime_spoofing_is_detected_from_content(
    auth_client: TestClient, filename: str, content: bytes, content_type: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = upload(
        auth_client, alice, filename=filename, content=content, content_type=content_type
    )

    assert response.status_code == 415
    assert _stored_files(auth_client) == []


def test_empty_file_and_missing_file_part_are_rejected(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    empty = upload(auth_client, alice, filename="empty.txt", content=b"", content_type="text/plain")
    no_file = auth_client.post("/api/v1/documents", headers=alice, data={"collection_ids": "[]"})
    wrong_field = auth_client.post(
        "/api/v1/documents",
        headers=alice,
        files={"document": ("a.pdf", pdf_bytes(), "application/pdf")},
    )

    assert empty.status_code == 422 and empty.json()["error"]["code"] == "INVALID_FILE"
    assert no_file.status_code == 422
    assert wrong_field.status_code == 422
    assert _stored_files(auth_client) == []


def test_non_multipart_body_is_rejected(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = auth_client.post("/api/v1/documents", headers=alice, json={"file": "x"})

    assert response.status_code in (400, 415, 422)
    assert _stored_files(auth_client) == []


def test_oversized_upload_is_rejected_and_nothing_is_stored(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(max_upload_bytes=4096)
    alice = make_user(client, "alice@example.com")
    big = b"a" * 5000

    response = upload(client, alice, filename="big.txt", content=big, content_type="text/plain")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert response.json()["error"]["details"] == {"max_bytes": 4096}
    assert _stored_files(client) == []
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0


def test_file_exactly_at_the_limit_is_accepted(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(max_upload_bytes=4096)
    alice = make_user(client, "alice@example.com")

    response = upload(
        client, alice, filename="edge.txt", content=b"a" * 4096, content_type="text/plain"
    )

    assert response.status_code == 201


def test_duplicate_content_is_rejected_per_user_and_leaves_one_file(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    first = upload_ok(auth_client, alice, filename="a.pdf", content=pdf_bytes("dup"))

    again = upload(auth_client, alice, filename="renamed.pdf", content=pdf_bytes("dup"))
    other_user = upload(auth_client, bob, filename="a.pdf", content=pdf_bytes("dup"))

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "DUPLICATE_DOCUMENT"
    assert again.json()["error"]["details"] == {"existing_document_id": first["id"]}
    assert other_user.status_code == 201  # duplicates are only detected within one user's documents
    assert len(_stored_files(auth_client)) == 2
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 2


def test_same_filename_with_different_content_is_allowed(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    first = upload_ok(auth_client, alice, filename="same.pdf", content=pdf_bytes("1"))
    second = upload_ok(auth_client, alice, filename="same.pdf", content=pdf_bytes("2"))

    assert first["id"] != second["id"]
    assert len(_stored_files(auth_client)) == 2


def test_storage_failure_returns_503_and_creates_no_record(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage.base import StorageError
    from app.storage.local import LocalStorageProvider

    async def broken_save(self: LocalStorageProvider, key: str, chunks: object) -> None:
        raise StorageError("disk full")

    monkeypatch.setattr(LocalStorageProvider, "save", broken_save)
    alice = make_user(auth_client, "alice@example.com")

    response = upload(auth_client, alice)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert "disk full" not in response.text
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0


def test_database_failure_after_storage_removes_the_stored_file(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.db.repositories.document_repository import DocumentRepository

    async def broken_add(self: DocumentRepository, document: object) -> None:
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    monkeypatch.setattr(DocumentRepository, "add", broken_add)
    alice = make_user(auth_client, "alice@example.com")

    response = upload(auth_client, alice)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "connection lost" not in response.text
    assert _stored_files(auth_client) == []
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0


def test_upload_is_throttled_per_user(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(rate_limit_upload_attempts=2)
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")

    statuses = [
        upload(client, alice, filename=f"f{i}.pdf", content=pdf_bytes(str(i))).status_code
        for i in range(4)
    ]
    bob_status = upload(client, bob).status_code

    assert statuses == [201, 201, 429, 429]
    assert bob_status == 201
    assert len(_stored_files(client)) == 3


def test_upload_creates_a_row_with_the_expected_owner_and_defaults(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    upload_ok(auth_client, alice)

    row = db_rows(
        migrated_database_url,
        "SELECT u.email, d.status, d.error_message FROM documents d "
        "JOIN users u ON u.id = d.user_id",
    )[0]

    assert (row[0], row[1], row[2]) == ("alice@example.com", "pending", None)
