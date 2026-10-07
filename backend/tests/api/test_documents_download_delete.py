import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tests.conftest import ClientFactory
from tests.files import (
    DOCX_TYPE,
    docx_bytes,
    make_user,
    markdown_bytes,
    pdf_bytes,
    text_bytes,
    upload_ok,
)
from tests.helpers import db_rows, db_scalar

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"


def _root(client: TestClient) -> Path:
    return Path(client.app.state.settings.storage_local_path)  # type: ignore[attr-defined]


def _files(client: TestClient) -> list[Path]:
    return [p for p in _root(client).rglob("*") if p.is_file()]


@pytest.mark.parametrize(
    ("filename", "content", "declared", "served_type"),
    [
        ("a.pdf", pdf_bytes(), "application/pdf", "application/pdf"),
        ("a.docx", docx_bytes(), DOCX_TYPE, DOCX_TYPE),
        ("a.txt", text_bytes(), "text/plain", "text/plain; charset=utf-8"),
        ("a.md", markdown_bytes(), "text/markdown", "text/markdown; charset=utf-8"),
    ],
)
def test_owner_downloads_the_exact_bytes(
    auth_client: TestClient, filename: str, content: bytes, declared: str, served_type: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice, filename=filename, content=content, content_type=declared)

    response = auth_client.get(f"{DOCS}/{doc['id']}/download", headers=alice)

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == served_type
    assert int(response.headers["content-length"]) == len(content)


def test_download_headers_are_safe(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice, filename="Résumé (final).pdf")

    response = auth_client.get(f"{DOCS}/{doc['id']}/download", headers=alice)

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "inline" not in disposition
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%20%28final%29.pdf" in disposition
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["x-request-id"]
    assert str(_root(auth_client)) not in str(dict(response.headers))


def test_download_filename_comes_from_sanitised_metadata_not_the_raw_upload_name(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice, filename='..\\..\\evil"\r\nSet-Cookie: x=1.pdf')

    response = auth_client.get(f"{DOCS}/{doc['id']}/download", headers=alice)

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert "\r" not in response.headers["content-disposition"]
    assert "/" not in doc["filename"] and "\\" not in doc["filename"]
    assert doc["filename"].startswith("evil") and doc["filename"].endswith(".pdf")


class _BodySpy:
    """ASGI middleware recording the size of every response body message it sees."""

    sizes: ClassVar[list[int]] = []

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def spying_send(message: Message) -> None:
            is_download_body = (
                scope["type"] == "http"
                and message["type"] == "http.response.body"
                and scope["path"].endswith("/download")
            )
            if is_download_body:
                _BodySpy.sizes.append(len(message.get("body", b"")))
            await send(message)

        await self.app(scope, receive, spying_send)


def test_large_download_is_streamed_in_bounded_chunks(auth_client_factory: ClientFactory) -> None:
    _BodySpy.sizes = []
    client = auth_client_factory(lambda app: app.add_middleware(_BodySpy))
    alice = make_user(client, "alice@example.com")
    big = (b"line of text\n" * 70_000)[:900_000]
    doc = upload_ok(client, alice, filename="big.txt", content=big, content_type="text/plain")

    response = client.get(f"{DOCS}/{doc['id']}/download", headers=alice)

    assert response.status_code == 200 and response.content == big
    sizes = [size for size in _BodySpy.sizes if size]
    assert len(sizes) > 5
    assert max(sizes) <= 64 * 1024


def test_download_when_the_stored_file_is_missing_reports_an_inconsistency(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)
    for path in _files(auth_client):
        path.unlink()

    response = auth_client.get(f"{DOCS}/{doc['id']}/download", headers=alice)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "STORAGE_INCONSISTENCY"
    assert str(_root(auth_client)) not in response.text
    assert "documents/" not in response.text


def test_metadata_is_still_available_when_the_file_is_missing(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)
    for path in _files(auth_client):
        path.unlink()

    assert auth_client.get(f"{DOCS}/{doc['id']}", headers=alice).status_code == 200


def test_delete_removes_record_file_and_collection_links(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.files import create_collection

    alice = make_user(auth_client, "alice@example.com")
    collection = create_collection(auth_client, alice)
    doc = upload_ok(auth_client, alice, collection_ids=[collection])
    assert len(_files(auth_client)) == 1

    response = auth_client.delete(f"{DOCS}/{doc['id']}", headers=alice)

    assert response.status_code == 204
    assert response.content == b""
    assert _files(auth_client) == []
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM document_collections") == 0
    assert auth_client.get(f"{DOCS}/{doc['id']}", headers=alice).status_code == 404
    assert auth_client.get(f"{DOCS}/{doc['id']}/download", headers=alice).status_code == 404


def test_deleting_twice_or_deleting_a_missing_document_is_404(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)

    assert auth_client.delete(f"{DOCS}/{doc['id']}", headers=alice).status_code == 204
    assert auth_client.delete(f"{DOCS}/{doc['id']}", headers=alice).status_code == 404
    assert auth_client.delete(f"{DOCS}/{uuid.uuid4()}", headers=alice).status_code == 404


def test_delete_proceeds_when_the_stored_file_is_already_gone(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)
    for path in _files(auth_client):
        path.unlink()

    response = auth_client.delete(f"{DOCS}/{doc['id']}", headers=alice)

    assert response.status_code == 204
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0


def test_delete_keeps_the_record_when_storage_cannot_delete(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage.base import StorageError
    from app.storage.local import LocalStorageProvider

    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)

    async def broken_delete(self: LocalStorageProvider, key: str) -> bool:
        raise StorageError("permission denied")

    monkeypatch.setattr(LocalStorageProvider, "delete", broken_delete)

    response = auth_client.delete(f"{DOCS}/{doc['id']}", headers=alice)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert "permission denied" not in response.text
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 1
    assert len(_files(auth_client)) == 1


def test_deleting_one_document_leaves_others_intact(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    keep = upload_ok(auth_client, alice, filename="keep.pdf", content=pdf_bytes("keep"))
    drop = upload_ok(auth_client, alice, filename="drop.pdf", content=pdf_bytes("drop"))

    auth_client.delete(f"{DOCS}/{drop['id']}", headers=alice)

    assert len(_files(auth_client)) == 1
    assert auth_client.get(f"{DOCS}/{keep['id']}/download", headers=alice).content == pdf_bytes(
        "keep"
    )


def test_deleting_the_account_row_removes_documents_but_not_other_users_files(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_execute

    alice = make_user(auth_client, "alice@example.com")
    bob = make_user(auth_client, "bob@example.com")
    upload_ok(auth_client, alice, content=pdf_bytes("a"))
    bobs = upload_ok(auth_client, bob, content=pdf_bytes("b"))

    db_execute(migrated_database_url, "DELETE FROM users WHERE email = 'alice@example.com'")

    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 1
    assert auth_client.get(f"{DOCS}/{bobs['id']}/download", headers=bob).status_code == 200


def test_rename_updates_only_the_display_name(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice, filename="old.pdf")
    key_before = db_scalar(migrated_database_url, "SELECT storage_key FROM documents")

    response = auth_client.patch(
        f"{DOCS}/{doc['id']}", headers=alice, json={"filename": "../New Name.PDF"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["filename"] == "New Name.PDF"
    assert db_scalar(migrated_database_url, "SELECT storage_key FROM documents") == key_before
    assert db_rows(migrated_database_url, "SELECT 1 FROM documents")


@pytest.mark.parametrize(
    "name", ["renamed.docx", "renamed.exe", "renamed", "", "   ", "..", "x" * 300]
)
def test_rename_cannot_change_the_file_type_or_use_unusable_names(
    auth_client: TestClient, name: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice, filename="old.pdf")

    response = auth_client.patch(f"{DOCS}/{doc['id']}", headers=alice, json={"filename": name})

    assert response.status_code in (415, 422)
    assert (
        auth_client.get(f"{DOCS}/{doc['id']}", headers=alice).json()["data"]["filename"]
        == "old.pdf"
    )
