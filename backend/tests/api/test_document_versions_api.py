import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory
from tests.files import make_user, upload_ok
from tests.helpers import bearer, create_user_in_db, db_execute, db_rows, db_scalar, login, tokens
from tests.processing_helpers import embed_now, fetch_document, process_now
from tests.search_helpers import ready_doc
from tests.version_helpers import (
    DOCS,
    active_id,
    new_version,
    ready_version,
    search_names,
    upload_version,
    version_rows,
)

pytestmark = pytest.mark.integration

V1 = "Rice manual edition one: seedlings are transplanted in July after the monsoon rain."
V2 = "Rice manual edition two: seedlings are transplanted in August after the monsoon rain."
V3 = "Rice manual edition three: seedlings are transplanted in September after the flood."
BLANK = b"   \n\n   \n"  # uploads fine, extracts to nothing: the processing fails


@pytest.fixture
def client(auth_client_factory: ClientFactory) -> TestClient:
    return auth_client_factory()


@pytest.fixture
def alice(client: TestClient) -> dict[str, str]:
    return make_user(client, "alice@example.com")


@pytest.fixture
def doc(client: TestClient, alice: dict[str, str]) -> str:
    return ready_doc(client, alice, V1, "manual.txt")


def api(client: TestClient, headers: dict[str, str], document: str) -> dict[str, Any]:
    return fetch_document(client, headers, document)


# --- the first version -------------------------------------------------------------------------


def test_a_first_upload_is_version_one_and_is_active_once_ready(
    client: TestClient, alice: dict[str, str]
) -> None:
    uploaded = upload_ok(client, alice, filename="a.txt", content=V1.encode(), content_type=None)

    assert uploaded["version_id"] == uploaded["id"] and uploaded["version_number"] == 1
    assert uploaded["version_count"] == 1 and uploaded["active_version_id"] is None
    assert uploaded["latest_version"] is None and uploaded["searchable"] is False

    process_now(client, uploaded["id"])
    embed_now(client, uploaded["id"])

    ready = api(client, alice, uploaded["id"])
    assert ready["active_version_id"] == uploaded["id"] and ready["searchable"] is True


# --- replacing --------------------------------------------------------------------------------


def test_a_replacement_is_a_new_version_and_the_old_one_stays_active_until_it_is_ready(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    response = upload_version(client, alice, doc, V2, "manual-2.txt")

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["created"] is True
    v2 = data["version"]
    assert v2["document_id"] == doc and v2["version_number"] == 2 and v2["id"] != doc
    assert v2["status"] == "pending" and v2["is_active"] is False and v2["searchable"] is False
    shown = api(client, alice, doc)
    assert shown["id"] == doc and shown["version_id"] == doc and shown["version_count"] == 2
    assert shown["active_version_id"] == doc and shown["status"] == "ready"
    assert (
        shown["latest_version"]["id"] == v2["id"] and shown["latest_version"]["status"] == "pending"
    )
    assert any("edition one" in t for t in search_names(client, alice, "rice transplanted"))

    process_now(client, v2["id"])
    embed_now(client, v2["id"])

    shown = api(client, alice, doc)
    assert shown["version_id"] == v2["id"] and shown["active_version_id"] == v2["id"]
    assert shown["latest_version"] is None and shown["filename"] == "manual-2.txt"
    texts = search_names(client, alice, "rice transplanted")
    assert texts and all("edition two" in t for t in texts)  # the old version is never returned
    assert [
        (r["number"], r["active"], r["current"]) for r in version_rows(migrated_database_url, doc)
    ] == [
        (1, False, False),
        (2, True, True),
    ]


def test_identical_content_is_idempotent_for_its_document_and_a_duplicate_for_another(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    other = ready_doc(client, alice, "A different document about fish ponds", "fish.txt")

    again = upload_version(client, alice, doc, V1, "same.txt")
    elsewhere = upload_version(client, alice, other, V1, "same.txt")

    assert again.status_code == 200
    assert again.json()["data"]["created"] is False and again.json()["data"]["version"]["id"] == doc
    assert elsewhere.status_code == 409
    error = elsewhere.json()["error"]
    assert error["code"] == "DUPLICATE_DOCUMENT" and error["details"]["existing_document_id"] == doc
    assert api(client, alice, doc)["version_count"] == 1


def test_content_equal_to_an_older_version_returns_that_version(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    v2 = ready_version(client, alice, doc, V2)

    again = upload_version(client, alice, doc, V1)

    assert again.status_code == 200 and again.json()["data"]["version"]["id"] == doc
    assert api(client, alice, doc)["version_id"] == v2  # nothing changed


def test_a_failed_replacement_leaves_the_previous_version_active_and_searchable(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    bad = new_version(client, alice, doc, BLANK)

    outcome = process_now(client, bad)

    assert outcome.status == "failed"
    shown = api(client, alice, doc)
    assert shown["status"] == "ready" and shown["searchable"] is True
    assert shown["version_id"] == doc and shown["active_version_id"] == doc
    assert shown["latest_version"]["status"] == "failed"
    assert shown["latest_version"]["failure_reason"] == "empty_document"
    assert any("edition one" in t for t in search_names(client, alice, "rice transplanted"))
    detail = client.get(f"{DOCS}/{doc}/versions/{bad}", headers=alice).json()["data"]
    assert detail["status"] == "failed" and detail["failure_reason"] and not detail["is_active"]
    assert active_id(migrated_database_url, doc) == doc


def test_a_failed_first_version_is_never_active(
    client: TestClient, alice: dict[str, str], migrated_database_url: str
) -> None:
    uploaded = upload_ok(client, alice, filename="b.txt", content=BLANK, content_type=None)
    process_now(client, uploaded["id"])

    assert api(client, alice, uploaded["id"])["active_version_id"] is None
    assert active_id(migrated_database_url, uploaded["id"]) is None


def test_retrying_a_failed_version_requeues_it_without_creating_a_version(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    bad = new_version(client, alice, doc, BLANK)
    process_now(client, bad)

    response = client.post(f"{DOCS}/{doc}/versions/{bad}/retry", headers=alice)

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "pending" and response.json()["data"]["id"] == bad
    assert api(client, alice, doc)["version_count"] == 2
    again = client.post(f"{DOCS}/{doc}/versions/{bad}/retry", headers=alice)
    assert again.status_code == 409  # no longer failed


def test_the_document_level_retry_targets_the_newest_failed_version(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    assert client.post(f"{DOCS}/{doc}/retry", headers=alice).status_code == 409
    bad = new_version(client, alice, doc, BLANK)
    process_now(client, bad)

    response = client.post(f"{DOCS}/{doc}/retry", headers=alice)

    assert response.status_code == 202
    assert response.json()["data"]["latest_version"]["status"] == "pending"
    assert response.json()["data"]["version_id"] == doc  # still shown as the active version


def test_a_late_older_version_never_replaces_a_newer_active_one(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    v2 = new_version(client, alice, doc, V2)
    v3 = new_version(client, alice, doc, V3)
    for vid in (v3, v2):  # the newer finishes first, the older one finishes late
        assert process_now(client, vid).status == "chunked"
        assert embed_now(client, vid).status == "ready"

    rows = version_rows(migrated_database_url, doc)
    assert [(r["number"], r["status"], r["active"]) for r in rows] == [
        (1, "ready", False),
        (2, "ready", False),
        (3, "ready", True),
    ]
    assert active_id(migrated_database_url, doc) == v3
    texts = search_names(client, alice, "rice transplanted")
    assert texts and all("edition three" in t for t in texts)


def test_a_newer_ready_version_activates_even_if_an_even_newer_one_failed(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    v2 = new_version(client, alice, doc, V2)
    v3 = new_version(client, alice, doc, BLANK)
    process_now(client, v3)

    process_now(client, v2)
    embed_now(client, v2)

    assert active_id(migrated_database_url, doc) == v2
    assert api(client, alice, doc)["latest_version"]["status"] == "failed"


# --- restore ----------------------------------------------------------------------------------


def test_a_retained_ready_version_can_be_restored_and_the_choice_is_idempotent(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    v2 = ready_version(client, alice, doc, V2)

    restored = client.post(f"{DOCS}/{doc}/versions/{doc}/activate", headers=alice)
    again = client.post(f"{DOCS}/{doc}/versions/{doc}/activate", headers=alice)

    assert restored.status_code == 200 and restored.json()["data"]["is_active"] is True
    assert again.status_code == 200
    assert active_id(migrated_database_url, doc) == doc
    assert api(client, alice, doc)["version_id"] == doc
    texts = search_names(client, alice, "rice transplanted")
    assert texts and all("edition one" in t for t in texts)
    v3 = ready_version(client, alice, doc, V3)  # a newer upload still activates by itself
    assert active_id(migrated_database_url, doc) == v3 and v2 != v3


def test_only_a_ready_version_can_be_activated(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    pending = new_version(client, alice, doc, V2)

    response = client.post(f"{DOCS}/{doc}/versions/{pending}/activate", headers=alice)

    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "VERSION_STATE_CONFLICT"
    )
    assert active_id(migrated_database_url, doc) == doc


# --- limits, listing and deleting versions --------------------------------------------------


def test_the_number_of_versions_is_bounded_and_a_refused_upload_stores_nothing(
    auth_client_factory: ClientFactory, tmp_path: Any
) -> None:
    limited = auth_client_factory(max_versions_per_document=2)
    alice = make_user(limited, "alice@example.com")
    first = ready_doc(limited, alice, V1, "m.txt")
    new_version(limited, alice, first, V2)
    storage = tmp_path / "storage"
    before = sum(1 for p in storage.rglob("*") if p.is_file())

    response = upload_version(limited, alice, first, V3)

    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_LIMIT"
    assert sum(1 for p in storage.rglob("*") if p.is_file()) == before


def test_versions_are_listed_newest_first_with_pagination(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    new_version(client, alice, doc, V2)
    new_version(client, alice, doc, V3)

    listed = client.get(f"{DOCS}/{doc}/versions", headers=alice, params={"page_size": 2})
    second = client.get(f"{DOCS}/{doc}/versions", headers=alice, params={"page_size": 2, "page": 2})

    assert [v["version_number"] for v in listed.json()["data"]] == [3, 2]
    assert [v["version_number"] for v in second.json()["data"]] == [1]
    assert listed.json()["meta"]["total_items"] == 3
    assert (
        client.get(f"{DOCS}/{doc}/versions", headers=alice, params={"page_size": 101}).status_code
        == 422
    )


def test_a_retained_version_can_be_deleted_with_its_file_chunks_and_vectors(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str, tmp_path: Any
) -> None:
    v2 = ready_version(client, alice, doc, V2)
    key = db_scalar(migrated_database_url, "SELECT storage_key FROM documents WHERE id = :i", i=doc)
    assert (tmp_path / "storage" / key).exists()

    response = client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice)

    assert response.status_code == 204
    assert not (tmp_path / "storage" / key).exists()
    assert (
        db_scalar(migrated_database_url, "SELECT count(*) FROM documents WHERE id = :i", i=doc) == 0
    )
    for table in ("document_chunks", "chunk_embeddings", "document_sections"):
        left = db_scalar(
            migrated_database_url,
            f"SELECT count(*) FROM {table} WHERE document_id = :i",
            i=doc,
        )
        assert left == 0
    assert (
        api(client, alice, doc)["version_id"] == v2
        and api(client, alice, doc)["version_count"] == 1
    )
    assert client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice).status_code == 404


def test_the_active_and_the_only_version_cannot_be_deleted(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    only = client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice)
    v2 = ready_version(client, alice, doc, V2)
    active = client.delete(f"{DOCS}/{doc}/versions/{v2}", headers=alice)

    assert only.status_code == 409 and active.status_code == 409
    assert api(client, alice, doc)["version_count"] == 2


def test_deleting_a_version_whose_file_is_already_gone_still_succeeds(
    client: TestClient, alice: dict[str, str], doc: str, tmp_path: Any, migrated_database_url: str
) -> None:
    ready_version(client, alice, doc, V2)
    key = db_scalar(migrated_database_url, "SELECT storage_key FROM documents WHERE id = :i", i=doc)
    (tmp_path / "storage" / key).unlink()

    assert client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice).status_code == 204


def test_deleting_a_document_removes_every_version_and_every_file(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str, tmp_path: Any
) -> None:
    ready_version(client, alice, doc, V2)
    new_version(client, alice, doc, V3)
    assert sum(1 for p in (tmp_path / "storage").rglob("*") if p.is_file()) == 3

    assert client.delete(f"{DOCS}/{doc}", headers=alice).status_code == 204

    assert sum(1 for p in (tmp_path / "storage").rglob("*") if p.is_file()) == 0
    for table in ("documents", "document_families", "document_chunks", "chunk_embeddings"):
        assert db_scalar(migrated_database_url, f"SELECT count(*) FROM {table}") == 0
    assert client.delete(f"{DOCS}/{doc}", headers=alice).status_code == 404  # repeating is safe


# --- collections and listing -------------------------------------------------------------------


def test_collection_membership_and_listing_follow_the_document_not_the_version(
    client: TestClient, alice: dict[str, str]
) -> None:
    from tests.files import create_collection

    collection = create_collection(client, alice, "Manuals")
    document = ready_doc(client, alice, V1, "m.txt", collection_ids=[collection])
    v2 = ready_version(client, alice, document, V2)

    listed = client.get(DOCS, headers=alice).json()
    in_collection = client.get(DOCS, headers=alice, params={"collection_id": collection}).json()

    assert [d["id"] for d in listed["data"]] == [document]  # one row per document
    assert listed["data"][0]["version_id"] == v2 and listed["data"][0]["version_count"] == 2
    assert [d["id"] for d in in_collection["data"]] == [document]
    assert listed["meta"]["total_items"] == 1
    scoped = search_names(client, alice, "rice transplanted", collection_id=collection)
    assert scoped and all("edition two" in t for t in scoped)
    by_id = search_names(client, alice, "rice transplanted", document_ids=[document])
    assert by_id and all("edition two" in t for t in by_id)


def test_search_filters_cannot_name_a_version_id(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    v2 = ready_version(client, alice, doc, V2)

    response = client.post(
        "/api/v1/search/hybrid", headers=alice, json={"query": "rice", "document_ids": [v2]}
    )

    assert response.status_code == 404  # a version id is not a document id


# --- isolation ----------------------------------------------------------------------------------


def test_another_users_documents_and_versions_are_a_404_for_every_operation(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    bob = make_user(client, "bob@example.com")
    v2 = ready_version(client, alice, doc, V2)
    own = ready_doc(client, bob, "Bob's own document about fish", "bob.txt")
    base = f"{DOCS}/{doc}/versions"

    attempts = [
        client.get(base, headers=bob),
        client.get(f"{base}/{v2}", headers=bob),
        client.post(base, headers=bob, files={"file": ("x.txt", b"hello world", "")}),
        client.post(f"{base}/{doc}/activate", headers=bob),
        client.post(f"{base}/{v2}/retry", headers=bob),
        client.delete(f"{base}/{doc}", headers=bob),
        client.get(
            f"{DOCS}/{own}/versions/{v2}", headers=bob
        ),  # alice's version under bob's document
        client.get(f"{DOCS}/{doc}/versions/{v2}"),
    ]

    assert [r.status_code for r in attempts[:-1]] == [404] * 7
    assert attempts[-1].status_code == 401
    assert api(client, alice, doc)["version_count"] == 2  # nothing happened


def test_administrators_have_no_access_to_other_users_versions(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    admin = bearer(tokens(login(client, email="admin@example.com"))["access_token"])

    response = client.get(f"{DOCS}/{doc}/versions", headers=admin)

    assert response.status_code == 404


def test_version_endpoints_validate_their_input(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    assert client.get(f"{DOCS}/not-a-uuid/versions", headers=alice).status_code == 422
    assert client.get(f"{DOCS}/{doc}/versions/not-a-uuid", headers=alice).status_code == 422
    assert client.post(f"{DOCS}/{doc}/versions", headers=alice).status_code in (400, 422)
    unsupported = upload_version(client, alice, doc, b"MZ\x90\x00 program", "evil.exe")
    assert unsupported.status_code in (415, 422)
    mismatched = upload_version(client, alice, doc, b"not a pdf at all", "fake.pdf")
    assert mismatched.status_code in (415, 422)
    assert api(client, alice, doc)["version_count"] == 1


def test_uploading_versions_is_rate_limited_with_the_upload_limit(
    auth_client_factory: ClientFactory,
) -> None:
    limited = auth_client_factory(rate_limit_upload_attempts=2, rate_limit_enabled=True)
    alice = make_user(limited, "alice@example.com")
    first = ready_doc(limited, alice, V1, "m.txt")

    statuses = [upload_version(limited, alice, first, f"{V2} {n}").status_code for n in range(3)]

    assert 429 in statuses


def test_an_oversized_replacement_is_refused_before_it_is_stored(
    auth_client_factory: ClientFactory, tmp_path: Any
) -> None:
    small = auth_client_factory(max_upload_bytes=2048)
    alice = make_user(small, "alice@example.com")
    first = ready_doc(small, alice, V1, "m.txt")
    before = sum(1 for p in (tmp_path / "storage").rglob("*") if p.is_file())

    response = upload_version(small, alice, first, "x" * 5000)

    assert response.status_code == 413
    assert sum(1 for p in (tmp_path / "storage").rglob("*") if p.is_file()) == before


# --- concurrency and recovery -------------------------------------------------------------------


def test_concurrent_replacements_both_succeed_with_distinct_version_numbers(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    results: list[Any] = []

    def go(text: str) -> None:
        results.append(upload_version(client, alice, doc, text))

    threads = [threading.Thread(target=go, args=(f"{V2} variant {n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r.status_code for r in results) == [201, 201, 201, 201]
    numbers = sorted(r["number"] for r in version_rows(migrated_database_url, doc))
    assert numbers == [1, 2, 3, 4, 5]
    assert active_id(migrated_database_url, doc) == doc


def test_concurrent_finalisation_leaves_exactly_the_newest_ready_version_active(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    ids = [new_version(client, alice, doc, f"{V2} concurrent {n}") for n in range(3)]
    for vid in ids:
        assert process_now(client, vid).status == "chunked"
    barrier = threading.Barrier(len(ids))
    outcomes: list[str] = []

    def finish(vid: str) -> None:
        barrier.wait()
        outcomes.append(embed_now(client, vid).status)

    threads = [threading.Thread(target=finish, args=(vid,)) for vid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes == ["ready"] * 3
    rows = version_rows(migrated_database_url, doc)
    assert [r["active"] for r in rows].count(True) == 1
    assert active_id(migrated_database_url, doc) == ids[-1]  # the highest version number


def test_a_crashed_worker_leaves_no_duplicate_artifacts_when_the_version_is_reprocessed(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    v2 = new_version(client, alice, doc, V2)
    process_now(client, v2)
    # the worker died in the middle of embedding: the claim is left behind and goes stale
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status = 'embedding', "
        "embedding_updated_at = now() - interval '1 day' "
        "WHERE id = :i",
        i=v2,
    )

    assert embed_now(client, v2).status == "ready"
    assert embed_now(client, v2).status in ("skipped", "ready")

    chunks = db_scalar(
        migrated_database_url, "SELECT count(*) FROM document_chunks WHERE document_id = :i", i=v2
    )
    vectors = db_scalar(
        migrated_database_url, "SELECT count(*) FROM chunk_embeddings WHERE document_id = :i", i=v2
    )
    assert chunks == vectors >= 1
    assert active_id(migrated_database_url, doc) == v2


def test_deleting_a_version_while_a_worker_processes_it_discards_the_work(
    client: TestClient,
    alice: dict[str, str],
    doc: str,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.parsers import registry

    victim = new_version(client, alice, doc, V2)
    real = registry.get_extractor

    def deleting(file_type: str) -> Any:
        extractor = real(file_type)
        original = extractor.extract

        def extract(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            worker = threading.Thread(
                target=lambda: db_execute(
                    migrated_database_url,
                    "DELETE FROM documents WHERE id = CAST(:i AS uuid)",
                    i=victim,
                )
            )
            worker.start()
            worker.join()
            return result

        extractor.extract = extract  # type: ignore[method-assign]
        return extractor

    monkeypatch.setattr("app.services.processing_service.get_extractor", deleting)

    outcome = process_now(client, victim)

    assert outcome.status in ("skipped", "failed")
    for table in ("document_sections", "document_chunks"):
        assert (
            db_scalar(
                migrated_database_url,
                f"SELECT count(*) FROM {table} WHERE document_id = :i",
                i=victim,
            )
            == 0
        )
    assert active_id(migrated_database_url, doc) == doc
    assert db_rows(migrated_database_url, "SELECT 1 FROM document_families WHERE id = :i", i=doc)


def test_a_document_stays_addable_to_collections_after_its_first_version_is_deleted(
    client: TestClient, alice: dict[str, str], doc: str
) -> None:
    from tests.files import create_collection

    ready_version(client, alice, doc, V2)
    assert client.delete(f"{DOCS}/{doc}/versions/{doc}", headers=alice).status_code == 204
    collection = create_collection(client, alice, "Later")

    added = client.post(
        f"/api/v1/collections/{collection}/documents", headers=alice, json={"document_ids": [doc]}
    )

    assert added.status_code in (200, 201), added.text
    listed = client.get(DOCS, headers=alice, params={"collection_id": collection}).json()["data"]
    assert [d["id"] for d in listed] == [doc]
    removed = client.delete(f"/api/v1/collections/{collection}/documents/{doc}", headers=alice)
    assert removed.status_code == 204


def test_version_events_are_logged_without_content_or_file_names(
    client: TestClient, alice: dict[str, str], doc: str, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.INFO)
    v2 = ready_version(client, alice, doc, V2, "secret-plan.txt")
    client.post(f"{DOCS}/{doc}/versions/{doc}/activate", headers=alice)
    client.delete(f"{DOCS}/{doc}/versions/{v2}", headers=alice)

    events = {r.getMessage() for r in caplog.records}
    assert {"document version uploaded", "document version activated"} <= events
    assert "document version deleted" in events
    everything = " ".join(
        f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}" for r in caplog.records
    )
    assert "secret-plan" not in everything and "edition two" not in everything


def test_an_embedding_failure_of_a_replacement_leaves_the_active_version_untouched(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    from functools import partial

    from app.embeddings.registry import get_provider, register_provider
    from tests.embedding_fakes import FailingProvider

    register_provider("test_fail_later", partial(FailingProvider, failures=0))
    client = auth_client_factory(embedding_provider="test_fail_later", embedding_max_attempts=1)
    alice = make_user(client, "alice@example.com")
    first = ready_doc(client, alice, V1, "m.txt")
    v2 = new_version(client, alice, first, V2)
    assert process_now(client, v2).status == "chunked"
    get_provider(client.app.state.settings)._remaining = 10**9  # type: ignore[attr-defined]

    outcome = embed_now(client, v2)

    assert outcome.status == "failed"
    rows = version_rows(migrated_database_url, first)
    assert [(r["number"], r["status"], r["active"]) for r in rows] == [
        (1, "ready", True),
        (2, "failed", False),
    ]
    shown = api(client, alice, first)
    assert shown["searchable"] is True and shown["latest_version"]["status"] == "failed"
    chunks = db_scalar(
        migrated_database_url, "SELECT count(*) FROM document_chunks WHERE document_id = :i", i=v2
    )
    assert chunks >= 1  # kept: a retry resumes at the embedding stage
    assert any("edition one" in t for t in search_names(client, alice, "rice transplanted"))


def test_reindexing_a_retained_newer_version_does_not_undo_a_restore(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    v2 = ready_version(client, alice, doc, V2)
    assert client.post(f"{DOCS}/{doc}/versions/{doc}/activate", headers=alice).status_code == 200
    # the retained newer version is embedded again (a new model, for example)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status = 'chunked' WHERE id = :i",
        i=v2,
    )

    assert embed_now(client, v2).status == "ready"

    assert active_id(migrated_database_url, doc) == doc


def test_an_older_ready_version_takes_over_when_the_active_one_is_no_longer_ready(
    client: TestClient, alice: dict[str, str], doc: str, migrated_database_url: str
) -> None:
    v2 = ready_version(client, alice, doc, V2)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status = 'failed', failure_reason = 'x', error_message = 'x' "
        "WHERE id = :i",
        i=v2,
    )

    again = client.post(f"{DOCS}/{doc}/versions/{v2}/activate", headers=alice)
    restored = client.post(f"{DOCS}/{doc}/versions/{doc}/activate", headers=alice)

    assert again.status_code == 409  # a failed version is not activated by calling it active
    assert restored.status_code == 200 and active_id(migrated_database_url, doc) == doc
