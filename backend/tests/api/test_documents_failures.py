from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.repositories.document_repository import DocumentRepository
from app.storage.base import StorageError, StoredObject
from app.storage.local import LocalStorageProvider
from tests.conftest import ClientFactory
from tests.files import make_user, pdf_bytes, upload, upload_ok
from tests.helpers import db_scalar

pytestmark = pytest.mark.integration


def _stored(client: TestClient) -> list[Path]:
    root = Path(client.app.state.settings.storage_local_path)  # type: ignore[attr-defined]
    return [p for p in root.rglob("*") if p.is_file()]


def test_concurrent_duplicate_upload_is_resolved_by_the_database_constraint(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    first = upload_ok(auth_client, alice, content=pdf_bytes("race"))
    real = DocumentRepository.get_by_checksum
    calls = {"count": 0}

    async def blind_first_check(self: DocumentRepository, user_id: object, checksum: str):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return None if calls["count"] == 1 else await real(self, user_id, checksum)  # type: ignore[arg-type]

    monkeypatch.setattr(DocumentRepository, "get_by_checksum", blind_first_check)

    response = upload(auth_client, alice, filename="again.pdf", content=pdf_bytes("race"))

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"existing_document_id": first["id"]}
    assert len(_stored(auth_client)) == 1  # the losing upload's file was cleaned up
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 1


def test_storage_key_collision_is_refused_without_overwriting(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.document_service.generate_storage_key", lambda: "documents/ab/" + "c" * 32
    )
    alice = make_user(auth_client, "alice@example.com")
    first = upload_ok(auth_client, alice, filename="a.pdf", content=pdf_bytes("one"))

    second = upload(auth_client, alice, filename="b.pdf", content=pdf_bytes("two"))

    assert second.status_code == 503
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 1
    download = auth_client.get(f"/api/v1/documents/{first['id']}/download", headers=alice)
    assert download.content == pdf_bytes("one")


def test_a_stored_object_that_differs_from_the_validated_upload_is_discarded(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_save = LocalStorageProvider.save

    async def corrupting_save(self: LocalStorageProvider, key: str, chunks: object) -> StoredObject:
        stored = await real_save(self, key, chunks)  # type: ignore[arg-type]
        return StoredObject(key=stored.key, size_bytes=stored.size_bytes, sha256="0" * 64)

    monkeypatch.setattr(LocalStorageProvider, "save", corrupting_save)
    alice = make_user(auth_client, "alice@example.com")

    response = upload(auth_client, alice)

    assert response.status_code == 503
    assert _stored(auth_client) == []
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 0


def test_download_reports_storage_unavailable_on_read_errors(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)

    async def broken_size(self: LocalStorageProvider, key: str) -> int:
        raise StorageError("io error at /secret/path")

    monkeypatch.setattr(LocalStorageProvider, "size", broken_size)

    response = auth_client.get(f"/api/v1/documents/{doc['id']}/download", headers=alice)

    assert response.status_code == 503
    assert "secret" not in response.text


def test_cleanup_failure_after_a_database_error_does_not_mask_the_error(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_add(self: DocumentRepository, document: object) -> None:
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    async def broken_delete(self: LocalStorageProvider, key: str) -> bool:
        raise StorageError("cannot delete")

    monkeypatch.setattr(DocumentRepository, "add", broken_add)
    monkeypatch.setattr(LocalStorageProvider, "delete", broken_delete)
    alice = make_user(auth_client, "alice@example.com")

    response = upload(auth_client, alice)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "cannot delete" not in response.text


def test_storage_quota_blocks_uploads_that_would_exceed_it(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(max_storage_bytes_per_user=2048)
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")
    first = upload(client, alice, filename="a.txt", content=b"a" * 1500, content_type="text/plain")

    over = upload(client, alice, filename="b.txt", content=b"b" * 1000, content_type="text/plain")
    other_user = upload(
        client, bob, filename="b.txt", content=b"b" * 1000, content_type="text/plain"
    )

    assert first.status_code == 201
    assert over.status_code == 403
    assert over.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert over.json()["error"]["details"] == {"quota_bytes": 2048}
    assert other_user.status_code == 201  # quotas are per user
    assert len(_stored(client)) == 2
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM documents") == 2


def test_deleting_documents_frees_quota(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(max_storage_bytes_per_user=2048)
    alice = make_user(client, "alice@example.com")
    first = upload_ok(
        client, alice, filename="a.txt", content=b"a" * 1500, content_type="text/plain"
    )
    blocked = upload(
        client, alice, filename="b.txt", content=b"b" * 1000, content_type="text/plain"
    )

    client.delete(f"/api/v1/documents/{first['id']}", headers=alice)
    retry = upload(client, alice, filename="b.txt", content=b"b" * 1000, content_type="text/plain")

    assert blocked.status_code == 403 and retry.status_code == 201


def test_duplicates_are_not_charged_against_the_quota(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(max_storage_bytes_per_user=2048)
    alice = make_user(client, "alice@example.com")
    upload_ok(client, alice, filename="a.txt", content=b"a" * 1500, content_type="text/plain")

    duplicate = upload(
        client, alice, filename="a2.txt", content=b"a" * 1500, content_type="text/plain"
    )

    assert duplicate.status_code == 409  # reported as a duplicate, not as a quota problem


def test_download_refuses_a_file_whose_size_no_longer_matches_the_record(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(auth_client, alice)
    for path in _stored(auth_client):
        path.write_bytes(path.read_bytes()[:10])  # simulate truncation or corruption

    response = auth_client.get(f"/api/v1/documents/{doc['id']}/download", headers=alice)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "STORAGE_INCONSISTENCY"
