import asyncio
import logging
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.embeddings.registry import get_provider, register_provider
from app.embeddings.types import EmbeddingConfig
from tests.conftest import ClientFactory
from tests.embedding_fakes import (
    BadVectorProvider,
    FailingProvider,
    HashProvider,
    HookProvider,
    SlowProvider,
    unavailable,
)
from tests.files import make_user, upload_ok
from tests.helpers import db_execute, db_rows, db_scalar
from tests.processing_helpers import (
    chunks_of,
    embed_now,
    embedding_row_of,
    fetch_document,
    process_now,
    rechunk_now,
    vectors_of,
)

pytestmark = pytest.mark.integration

COUNT_VECTORS = "SELECT count(*) FROM chunk_embeddings WHERE document_id = :d"
SET_CHUNKED = "UPDATE documents SET status = 'chunked' WHERE id = :d"


def _long_text(paragraphs: int = 12) -> bytes:
    return "\n\n".join(
        f"Paragraph {i}. " + "The quick brown fox jumps over the lazy dog. " * 6
        for i in range(paragraphs)
    ).encode()


def _chunked(client: TestClient, headers: dict[str, str], data: bytes | None = None) -> str:
    doc_id = str(
        upload_ok(
            client, headers, filename="n.txt", content=data or _long_text(), content_type=None
        )["id"]
    )
    assert process_now(client, doc_id).status == "chunked"
    return doc_id


def _use(name: str, factory: Callable[[EmbeddingConfig, Any], Any]) -> dict[str, str]:
    """Register a provider and return the settings overrides that select it."""
    register_provider(name, factory)
    return {"embedding_provider": name}


def _chunk_count(db: str, doc_id: str) -> int:
    return len(chunks_of(db, doc_id))


# --- the happy path ------------------------------------------------------------------------


def test_chunked_document_is_embedded_and_becomes_searchable(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    chunk_count = _chunk_count(migrated_database_url, doc_id)

    outcome = embed_now(auth_client, doc_id)

    assert outcome.status == "ready"
    vectors = vectors_of(migrated_database_url, doc_id)
    assert len(vectors) == chunk_count > 3
    assert {v["dims"] for v in vectors} == {384}
    assert all(v["text_sha256"] == v["chunk_sha256"] for v in vectors)
    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["status"] == "ready" and row["embedded_chunk_count"] == chunk_count
    assert row["completed_at"] is not None and row["config_id"] == vectors[0]["config_id"]
    document = fetch_document(auth_client, alice, doc_id)
    assert document["searchable"] is True and document["embedded_chunk_count"] == chunk_count


def test_batches_never_exceed_the_configured_size(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(
        chunking_max_chars=200, chunking_overlap_chars=20, embedding_batch_size=3
    )
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    chunk_count = _chunk_count(migrated_database_url, doc_id)
    assert chunk_count > 6

    embed_now(client, doc_id)

    provider = get_provider(client.app.state.settings)  # type: ignore[attr-defined]
    assert isinstance(provider, HashProvider)
    assert max(provider.calls) <= 3 and sum(provider.calls) == chunk_count
    assert provider.warmups >= 1


def test_embedding_an_already_ready_document_is_a_no_op(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    embed_now(auth_client, doc_id)
    before = vectors_of(migrated_database_url, doc_id)

    outcome = embed_now(auth_client, doc_id)

    assert (outcome.status, outcome.reason) == ("skipped", "not_claimable")
    assert vectors_of(migrated_database_url, doc_id) == before


def test_embedding_a_document_that_was_never_chunked_is_skipped(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    uploaded = upload_ok(
        auth_client, alice, filename="n.txt", content=_long_text(), content_type=None
    )
    doc_id = str(uploaded["id"])

    assert embed_now(auth_client, doc_id).status == "skipped"
    assert db_scalar(migrated_database_url, COUNT_VECTORS, d=doc_id) == 0


def test_chunk_truncation_is_recorded_on_the_vector(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(chunking_max_chars=5000, chunking_overlap_chars=0)
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice, data=("word " * 700).encode())  # one 3,500 character chunk

    embed_now(client, doc_id)

    assert [v["truncated"] for v in vectors_of(migrated_database_url, doc_id)] == [True]


# --- concurrency and idempotency --------------------------------------------------------


def test_concurrent_workers_never_embed_a_document_twice(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from app.workers.embedding_tasks import _embed

    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    settings = auth_client.app.state.settings  # type: ignore[attr-defined]

    async def race() -> list[Any]:
        return list(
            await asyncio.gather(*(_embed(settings, uuid.UUID(doc_id), f"t{i}") for i in range(4)))
        )

    outcomes = asyncio.run(race())

    assert sorted(o.status for o in outcomes) == ["ready", "skipped", "skipped", "skipped"]
    vectors = vectors_of(migrated_database_url, doc_id)
    assert len(vectors) == _chunk_count(migrated_database_url, doc_id)
    assert len({v["chunk_id"] for v in vectors}) == len(vectors)
    provider = get_provider(settings)
    assert isinstance(provider, HashProvider) and sum(provider.calls) == len(vectors)


def test_a_rerun_of_a_completed_document_adds_nothing(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    embed_now(auth_client, doc_id)
    db_execute(migrated_database_url, SET_CHUNKED, d=doc_id)
    before = vectors_of(migrated_database_url, doc_id)

    # nothing is left to embed, so the run just completes the document again
    assert embed_now(auth_client, doc_id).status == "ready"

    assert vectors_of(migrated_database_url, doc_id) == before


# --- bounded jobs and resumption ---------------------------------------------------------


def test_job_stops_at_its_time_budget_and_the_next_one_resumes(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    overrides = _use("test_slow", partial(SlowProvider, delay=0.4))
    client = auth_client_factory(
        chunking_max_chars=200, chunking_overlap_chars=20, embedding_batch_size=2, **overrides
    )
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    total = _chunk_count(migrated_database_url, doc_id)

    first = embed_now(client, doc_id, embedding_task_budget_seconds=1)

    assert first.status == "continue"
    row = embedding_row_of(migrated_database_url, doc_id)
    done = len(vectors_of(migrated_database_url, doc_id))
    assert row["status"] == "chunked" and 0 < done < total
    assert row["embedded_chunk_count"] == done and row["embedding_attempts"] == 1
    assert embed_now(client, doc_id).status == "ready"
    assert len(vectors_of(migrated_database_url, doc_id)) == total
    assert embedding_row_of(migrated_database_url, doc_id)["embedding_attempts"] == 1


# --- failures ----------------------------------------------------------------------------


def test_transient_failure_is_retried_and_then_succeeds(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(**_use("test_flaky", partial(FailingProvider, failures=1)))
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)

    first = embed_now(client, doc_id)

    assert (first.status, first.reason) == ("retry", "embedding_failed")
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "chunked"
    assert _chunk_count(migrated_database_url, doc_id) > 0
    assert embed_now(client, doc_id).status == "ready"


def test_retries_are_bounded_and_end_in_a_failed_document_that_keeps_its_chunks(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(
        embedding_max_attempts=2, **_use("test_always_fails", FailingProvider)
    )
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    chunks_before = _chunk_count(migrated_database_url, doc_id)

    outcomes = [embed_now(client, doc_id).status for _ in range(2)]

    assert outcomes == ["retry", "failed"]
    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["status"] == "failed" and row["failure_reason"] == "embedding_failed"
    assert row["error_message"] and "simulated" not in row["error_message"]
    assert _chunk_count(migrated_database_url, doc_id) == chunks_before == row["chunk_count"]
    assert vectors_of(migrated_database_url, doc_id) == []


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (partial(FailingProvider, warmup_error=unavailable()), "model_unavailable"),
        (partial(BadVectorProvider, kind="nan"), "invalid_embedding"),
        (partial(BadVectorProvider, kind="zero"), "invalid_embedding"),
        (partial(BadVectorProvider, kind="dims"), "invalid_embedding"),
        (
            partial(FailingProvider, error=lambda: RuntimeError("secret detail /srv/x")),
            "embedding_failed",
        ),
    ],
    ids=["model-unavailable", "nan", "zeros", "wrong-dimension", "unexpected"],
)
def test_terminal_failures_fail_once_with_a_safe_message(
    auth_client_factory: ClientFactory,
    migrated_database_url: str,
    provider: Callable[..., Any],
    reason: str,
) -> None:
    client = auth_client_factory(**_use(f"test_{uuid.uuid4().hex[:6]}", provider))
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)

    outcome = embed_now(client, doc_id)

    assert (outcome.status, outcome.reason) == ("failed", reason)
    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["failure_reason"] == reason and row["status"] == "failed"
    assert "secret" not in str(row["error_message"]) and "/srv" not in str(row["error_message"])
    assert db_scalar(migrated_database_url, COUNT_VECTORS, d=doc_id) == 0


def test_a_retryable_model_load_failure_is_retried(
    auth_client_factory: ClientFactory,
) -> None:
    provider = partial(FailingProvider, failures=0, warmup_error=unavailable(retryable=True))
    client = auth_client_factory(**_use("test_flaky_load", provider))
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)

    outcome = embed_now(client, doc_id)

    assert (outcome.status, outcome.reason) == ("retry", "model_unavailable")


def test_manual_retry_after_an_embedding_failure_resumes_at_chunked(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = auth_client_factory(**_use("test_retry_api", partial(FailingProvider, failures=1)))
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    assert embed_now(client, doc_id, embedding_max_attempts=1).status == "failed"

    response = client.post(f"/api/v1/documents/{doc_id}/retry", headers=alice)

    assert response.status_code == 202 and response.json()["data"]["status"] == "chunked"
    assert _chunk_count(migrated_database_url, doc_id) > 0  # no re-extraction or re-chunking
    assert embed_now(client, doc_id).status == "ready"


def test_a_document_without_chunks_fails_explicitly_instead_of_sticking(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    db_execute(
        migrated_database_url, "DELETE FROM document_chunks WHERE document_id = :d", d=doc_id
    )
    db_execute(
        migrated_database_url, "UPDATE documents SET chunk_count = 0 WHERE id = :d", d=doc_id
    )

    outcome = embed_now(auth_client, doc_id)

    assert (outcome.status, outcome.reason) == ("failed", "empty_document")


def test_document_deleted_while_embedding_is_discarded_cleanly(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    holder: dict[str, str] = {}

    def delete_after_first_batch(calls: int) -> None:
        if calls == 1:
            db_execute(migrated_database_url, "DELETE FROM documents WHERE id = :d", d=holder["id"])

    overrides = _use(
        "test_delete_midway", lambda cfg, rt: HookProvider(cfg, delete_after_first_batch)
    )
    client = auth_client_factory(
        chunking_max_chars=200, chunking_overlap_chars=20, embedding_batch_size=2, **overrides
    )
    alice = make_user(client, "alice@example.com")
    holder["id"] = doc_id = _chunked(client, alice)

    outcome = embed_now(client, doc_id)

    assert outcome.status == "skipped"
    assert db_scalar(migrated_database_url, COUNT_VECTORS, d=doc_id) == 0


# --- re-embedding -----------------------------------------------------------------------


def test_vectors_whose_chunk_text_changed_are_replaced(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    embed_now(auth_client, doc_id)
    fresh = "f" * 64
    db_execute(
        migrated_database_url,
        "UPDATE document_chunks SET text_sha256 = :h WHERE document_id = :d AND chunk_index = 0",
        h=fresh,
        d=doc_id,
    )
    db_execute(migrated_database_url, SET_CHUNKED, d=doc_id)

    assert embed_now(auth_client, doc_id).status == "ready"

    first = vectors_of(migrated_database_url, doc_id)[0]
    assert first["text_sha256"] == fresh == first["chunk_sha256"]
    assert len(vectors_of(migrated_database_url, doc_id)) == _chunk_count(
        migrated_database_url, doc_id
    )


def test_rechunking_a_searchable_document_drops_its_vectors_and_it_is_embedded_again(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    embed_now(auth_client, doc_id)

    outcome = rechunk_now(auth_client, doc_id, chunking_max_chars=300, chunking_overlap_chars=30)

    assert outcome.status == "chunked"
    assert vectors_of(migrated_database_url, doc_id) == []
    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["status"] == "chunked" and row["embedded_chunk_count"] is None
    assert row["config_id"] is None
    assert embed_now(auth_client, doc_id).status == "ready"
    assert len(vectors_of(migrated_database_url, doc_id)) == _chunk_count(
        migrated_database_url, doc_id
    )


def test_changing_the_model_revision_requires_an_explicit_reindex(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from app.workers.embedding_tasks import _reindex

    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    embed_now(auth_client, doc_id)
    old_config = embedding_row_of(migrated_database_url, doc_id)["config_id"]
    new_settings = auth_client.app.state.settings.model_copy(  # type: ignore[attr-defined]
        update={"embedding_model_revision": "test-revision-2"}
    )

    # nothing happens by itself: the document is still ready under the old configuration
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "ready"
    queued: list[uuid.UUID] = []
    assert asyncio.run(_reindex(new_settings, queued.append, 50, False)) >= 1
    assert uuid.UUID(doc_id) in queued

    assert embed_now(auth_client, doc_id, embedding_model_revision="test-revision-2").status == (
        "ready"
    )

    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["config_id"] != old_config
    vectors = vectors_of(migrated_database_url, doc_id)
    assert {v["config_id"] for v in vectors} == {row["config_id"]}  # old vectors were dropped
    assert len(vectors) == row["chunk_count"]
    configs = db_scalar(
        migrated_database_url,
        "SELECT count(*) FROM embedding_configs WHERE id::text = :c",
        c=old_config,
    )
    assert configs == 1  # the old configuration row stays as history
    again: list[uuid.UUID] = []
    asyncio.run(_reindex(new_settings, again.append, 50, False))
    assert uuid.UUID(doc_id) not in again  # already under the active configuration


# --- recovery ---------------------------------------------------------------------------


def _sweep(client: TestClient) -> tuple[Any, list[uuid.UUID]]:
    from app.workers.embedding_tasks import _recover

    queued: list[uuid.UUID] = []
    report = asyncio.run(_recover(client.app.state.settings, queued.append))  # type: ignore[attr-defined]
    return report, queued


def test_sweep_releases_a_stalled_embedding_and_requeues_it(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status='embedding', embedding_attempts=1, "
        "embedding_updated_at = now() - interval '2 hours' WHERE id = :d",
        d=doc_id,
    )

    report, queued = _sweep(auth_client)

    assert report["released"] == 1 and uuid.UUID(doc_id) in queued
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "chunked"
    assert embed_now(auth_client, doc_id).status == "ready"


def test_sweep_abandons_a_document_that_keeps_crashing_its_worker(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status='embedding', embedding_attempts=3, "
        "embedding_updated_at = now() - interval '2 hours' WHERE id = :d",
        d=doc_id,
    )

    report, _ = _sweep(auth_client)

    row = embedding_row_of(migrated_database_url, doc_id)
    assert report["abandoned"] == 1
    assert row["status"] == "failed" and row["failure_reason"] == "retries_exhausted"
    assert row["chunk_count"] is not None


def test_sweep_leaves_a_live_worker_alone_and_queues_forgotten_chunked_documents(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    live = _chunked(auth_client, alice, data=_long_text(3))
    forgotten = _chunked(auth_client, alice, data=_long_text(4))
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status='embedding', embedding_attempts=1, "
        "embedding_updated_at = now() WHERE id = :d",
        d=live,
    )
    db_execute(
        migrated_database_url,
        "UPDATE documents SET updated_at = now() - interval '1 hour' WHERE id = :d",
        d=forgotten,
    )

    report, queued = _sweep(auth_client)

    assert report["released"] == 0 and uuid.UUID(live) not in queued
    assert uuid.UUID(forgotten) in queued
    assert embedding_row_of(migrated_database_url, live)["status"] == "embedding"


def test_soft_time_limit_releases_the_document_for_another_attempt(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from celery.exceptions import SoftTimeLimitExceeded

    from app.workers import embedding_tasks
    from app.workers.celery_app import create_celery_app

    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status='embedding', embedding_attempts=1, "
        "embedding_updated_at=now() WHERE id=:d",
        d=doc_id,
    )

    async def interrupted(*args: object, **kwargs: object) -> None:
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(embedding_tasks, "_embed", interrupted)
    create_celery_app(auth_client.app.state.settings, configure_logs=False).set_current()  # type: ignore[attr-defined]

    result = embedding_tasks.embed_document.apply(args=[doc_id])

    assert result.get() == {"status": "retry", "reason": "timeout"}
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "chunked"


def test_embed_task_ignores_invalid_ids(auth_client: TestClient) -> None:
    from app.workers import embedding_tasks
    from app.workers.celery_app import create_celery_app

    create_celery_app(auth_client.app.state.settings, configure_logs=False).set_current()  # type: ignore[attr-defined]

    result = embedding_tasks.embed_document.apply(args=["nope"]).get()

    assert result == {"status": "skipped", "reason": "invalid_id"}


# --- cleanup and privacy ---------------------------------------------------------------


def test_deleting_a_document_removes_its_vectors(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    embed_now(auth_client, doc_id)
    assert vectors_of(migrated_database_url, doc_id)

    assert auth_client.delete(f"/api/v1/documents/{doc_id}", headers=alice).status_code == 204

    assert db_scalar(migrated_database_url, COUNT_VECTORS, d=doc_id) == 0


def test_logs_report_embedding_metrics_but_never_text(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    marker = "ZEBRA-CONFIDENTIAL-MARKER"
    doc_id = _chunked(auth_client, alice, data=f"{marker} appears here. ".encode() * 30)

    with caplog.at_level(logging.DEBUG):
        embed_now(auth_client, doc_id)

    assert marker not in caplog.text
    done = [r for r in caplog.records if r.getMessage() == "embedding completed"]
    assert done and getattr(done[0], "chunks_this_run", 0) > 0
    assert getattr(done[0], "batches", 0) >= 1 and hasattr(done[0], "chunks_per_second")


def test_phase_five_chunked_documents_are_embeddable_without_any_conversion(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)  # exactly what a Phase 5 database contains

    row = db_rows(
        migrated_database_url,
        "SELECT embedding_attempts, embedded_chunk_count, embedding_config_id "
        "FROM documents WHERE id = :d",
        d=doc_id,
    )[0]
    assert tuple(row) == (0, None, None)
    assert embed_now(auth_client, doc_id).status == "ready"
