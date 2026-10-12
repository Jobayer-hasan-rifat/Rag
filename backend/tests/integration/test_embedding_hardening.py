import asyncio
import time
import uuid
from functools import partial
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient

from app.embeddings.registry import register_provider
from app.embeddings.types import EmbeddingFailedError, Vectors
from tests.conftest import ClientFactory
from tests.embedding_fakes import FailingProvider, HashProvider, HookProvider
from tests.files import make_user, upload_ok
from tests.helpers import db_execute
from tests.processing_helpers import embed_now, embedding_row_of, process_now, vectors_of

pytestmark = pytest.mark.integration

SEARCH = "/api/v1/search/semantic"


def _chunked(client: TestClient, headers: dict[str, str], paragraphs: int = 12) -> str:
    text = "\n\n".join(
        f"Paragraph {i}. " + "The quick brown fox jumps. " * 8 for i in range(paragraphs)
    )
    doc_id = str(
        upload_ok(client, headers, filename="n.txt", content=text.encode(), content_type=None)["id"]
    )
    assert process_now(client, doc_id).status == "chunked"
    return doc_id


def test_a_search_does_not_hold_a_database_connection_while_the_model_runs(
    auth_client_factory: ClientFactory,
) -> None:
    seen: list[int] = []
    holder: dict[str, Any] = {}

    class Probe(HashProvider):
        def embed_query(self, text: str) -> Vectors:
            seen.append(holder["client"].app.state.engine.pool.checkedout())
            return super().embed_query(text)

    register_provider("test_probe", lambda cfg, rt: Probe(cfg))
    client = auth_client_factory(embedding_provider="test_probe")
    holder["client"] = client
    alice = make_user(client, "alice@example.com")

    assert client.post(SEARCH, headers=alice, json={"query": "anything"}).status_code == 200

    assert seen == [0]  # authentication and validation are done; the connection went back


def test_progress_resets_attempts_so_a_late_transient_failure_is_retried_not_failed(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    def fail_second_batch(calls: int) -> None:
        if calls == 1:
            raise EmbeddingFailedError("transient")

    register_provider("test_late_failure", lambda cfg, rt: HookProvider(cfg, fail_second_batch))
    client = auth_client_factory(
        embedding_provider="test_late_failure",
        chunking_max_chars=200,
        chunking_overlap_chars=20,
        embedding_batch_size=2,
        embedding_max_attempts=3,
    )
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    # two earlier claims achieved nothing, so this is the document's third
    db_execute(
        migrated_database_url, "UPDATE documents SET embedding_attempts = 2 WHERE id = :d", d=doc_id
    )

    outcome = embed_now(client, doc_id)

    assert (outcome.status, outcome.reason) == ("retry", "embedding_failed")
    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["status"] == "chunked" and row["embedding_attempts"] == 1
    assert len(vectors_of(migrated_database_url, doc_id)) == 2  # the first batch was kept


def test_the_time_budget_starts_after_the_model_is_loaded(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    class SlowLoad(HashProvider):
        def warmup(self) -> None:
            time.sleep(1.3)
            super().warmup()

    register_provider("test_slow_load", lambda cfg, rt: SlowLoad(cfg))
    client = auth_client_factory(embedding_provider="test_slow_load")
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice, paragraphs=3)

    outcome = embed_now(client, doc_id, embedding_task_budget_seconds=1)

    assert outcome.status == "ready"  # loading took longer than the budget and cost nothing


def test_a_soft_time_limit_inside_the_model_call_is_a_retry_not_a_failure(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    from app.workers import embedding_tasks
    from app.workers.celery_app import create_celery_app

    def time_limit(calls: int) -> None:
        raise SoftTimeLimitExceeded

    register_provider("test_time_limit", lambda cfg, rt: HookProvider(cfg, time_limit))
    client = auth_client_factory(embedding_provider="test_time_limit")
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    create_celery_app(client.app.state.settings, configure_logs=False).set_current()  # type: ignore[attr-defined]

    result = embedding_tasks.embed_document.apply(args=[doc_id]).get()

    assert result == {"status": "retry", "reason": "timeout"}
    row = embedding_row_of(migrated_database_url, doc_id)
    assert row["status"] == "chunked" and row["failure_reason"] is None


def test_a_vector_count_mismatch_fails_the_document_instead_of_marking_it_ready(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET chunk_count = chunk_count + 1 WHERE id = :d",
        d=doc_id,
    )

    outcome = embed_now(auth_client, doc_id)

    assert (outcome.status, outcome.reason) == ("failed", "embedding_failed")
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "failed"


def test_embedding_a_document_deleted_before_completion_is_skipped(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    holder: dict[str, str] = {}

    def delete_everything_but_keep_going(calls: int) -> None:
        db_execute(
            migrated_database_url,
            "UPDATE documents SET status = 'failed', failure_reason = 'x' WHERE id = :d",
            d=holder["id"],
        )

    register_provider(
        "test_changed", lambda cfg, rt: HookProvider(cfg, delete_everything_but_keep_going)
    )
    client = auth_client_factory(embedding_provider="test_changed")
    alice = make_user(client, "alice@example.com")
    holder["id"] = doc_id = _chunked(client, alice, paragraphs=3)

    outcome = embed_now(client, doc_id)

    assert (outcome.status, outcome.reason) == ("skipped", "document_changed")


def test_reindex_can_pick_up_failed_embeddings(
    auth_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    from app.workers.embedding_tasks import _reindex

    register_provider("test_fail_once", partial(FailingProvider, failures=1))
    client = auth_client_factory(embedding_provider="test_fail_once", embedding_max_attempts=1)
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice)
    assert embed_now(client, doc_id).status == "failed"
    settings = client.app.state.settings  # type: ignore[attr-defined]
    queued: list[uuid.UUID] = []

    assert asyncio.run(_reindex(settings, queued.append, 50, False)) == 0
    assert asyncio.run(_reindex(settings, queued.append, 50, True)) >= 1

    assert uuid.UUID(doc_id) in queued
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "chunked"
    assert embed_now(client, doc_id).status == "ready"


def test_timeout_handling_ignores_documents_that_are_not_embedding(
    auth_client: TestClient,
) -> None:
    from app.workers.embedding_tasks import _handle_timeout

    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)

    outcome = asyncio.run(
        _handle_timeout(auth_client.app.state.settings, uuid.UUID(doc_id), "t")  # type: ignore[attr-defined]
    )

    assert (outcome.status, outcome.reason) == ("skipped", "not_embedding")


def test_the_sweep_survives_a_broker_that_refuses_messages(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from app.workers.embedding_tasks import _recover

    alice = make_user(auth_client, "alice@example.com")
    doc_id = _chunked(auth_client, alice)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET updated_at = now() - interval '1 hour' WHERE id = :d",
        d=doc_id,
    )

    def refuse(document_id: uuid.UUID) -> None:
        raise ConnectionError("broker down")

    report = asyncio.run(_recover(auth_client.app.state.settings, refuse))  # type: ignore[attr-defined]

    assert report["requeued"] == 0  # reported honestly, and the document stays chunked
    assert embedding_row_of(migrated_database_url, doc_id)["status"] == "chunked"


def test_hnsw_search_settings_are_applied_when_configured(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(search_hnsw_ef_search=64)
    alice = make_user(client, "alice@example.com")
    doc_id = _chunked(client, alice, paragraphs=3)
    assert embed_now(client, doc_id).status == "ready"

    response = client.post(SEARCH, headers=alice, json={"query": "quick brown fox"})

    assert response.status_code == 200 and response.json()["data"]["results"]
