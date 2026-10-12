import json
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from celery import Celery
from fastapi.testclient import TestClient

from app.embeddings.registry import register_provider
from app.workers.dispatch import EMBED_DOCUMENT_TASK
from tests.conftest import ClientFactory
from tests.embedding_fakes import FailingProvider
from tests.files import make_user, upload_ok
from tests.helpers import db_execute
from tests.pdf_factory import make_pdf
from tests.processing_helpers import embedding_row_of, fetch_document, process_now, vectors_of

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"
SEARCH = "/api/v1/search/semantic"


def wait_for(
    client: TestClient,
    headers: dict[str, str],
    doc_id: str,
    wanted: set[str],
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    data: dict[str, Any] = {}
    while time.monotonic() < deadline:
        data = fetch_document(client, headers, doc_id)
        if data["status"] in wanted:
            return data
        time.sleep(0.2)
    raise AssertionError(f"document stayed in {data.get('status')!r}, wanted {wanted}")


def _upload(client: TestClient, headers: dict[str, str], name: str, data: bytes) -> str:
    return str(upload_ok(client, headers, filename=name, content=data, content_type=None)["id"])


def test_upload_flows_through_the_workers_to_searchable(
    embedding_pipeline_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = embedding_pipeline_client_factory()
    alice = make_user(client, "alice@example.com")
    pdf = make_pdf(["ইলিশ বাংলাদেশের জাতীয় মাছ", "Rice is planted during the monsoon season"])

    doc_id = _upload(client, alice, "mixed.pdf", pdf)
    done = wait_for(client, alice, doc_id, {"ready", "failed"})

    assert done["status"] == "ready" and done["searchable"] is True
    assert (
        done["embedded_chunk_count"]
        == done["chunk_count"]
        == len(vectors_of(migrated_database_url, doc_id))
    )
    response = client.post(SEARCH, headers=alice, json={"query": "rice monsoon season"})
    assert response.status_code == 200
    top = response.json()["data"]["results"][0]
    assert top["document_id"] == doc_id and top["page_number"] == 2


def test_documents_wait_in_chunked_while_no_embedding_worker_runs(
    pipeline_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    client = pipeline_client_factory()  # consumes processing queues only
    alice = make_user(client, "alice@example.com")

    doc_id = _upload(client, alice, "n.txt", b"waiting for the embedding worker " * 5)
    done = wait_for(client, alice, doc_id, {"chunked", "failed"})

    assert done["status"] == "chunked" and done["searchable"] is False
    assert (
        client.post(SEARCH, headers=alice, json={"query": "waiting"}).json()["data"]["results"]
        == []
    )


def test_a_failed_embedding_is_retried_through_the_queue_without_rechunking(
    embedding_pipeline_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    register_provider("test_flaky_worker", partial(FailingProvider, failures=1))
    client = embedding_pipeline_client_factory(
        embedding_provider="test_flaky_worker", embedding_max_attempts=1
    )
    alice = make_user(client, "alice@example.com")

    doc_id = _upload(client, alice, "n.txt", b"retry the embedding stage only " * 5)
    failed = wait_for(client, alice, doc_id, {"failed", "ready"})
    assert failed["status"] == "failed" and failed["failure_reason"] == "embedding_failed"
    assert failed["chunk_count"] and failed["searchable"] is False

    response = client.post(f"{DOCS}/{doc_id}/retry", headers=alice)

    assert response.status_code == 202 and response.json()["data"]["status"] == "chunked"
    assert wait_for(client, alice, doc_id, {"ready", "failed"})["status"] == "ready"
    assert embedding_row_of(migrated_database_url, doc_id)["embedding_attempts"] == 1


def test_a_lost_hand_off_is_recovered_by_the_sweep(
    embedding_pipeline_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    from app.workers.celery_app import create_celery_app
    from app.workers.embedding_tasks import recover_stalled_embeddings

    client = embedding_pipeline_client_factory()
    alice = make_user(client, "alice@example.com")
    doc_id = _upload(client, alice, "n.txt", b"lost message " * 5)
    wait_for(client, alice, doc_id, {"ready"})
    # put the document back where it would be if its embedding message had been lost
    db_execute(
        migrated_database_url, "DELETE FROM chunk_embeddings WHERE document_id = :d", d=doc_id
    )
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status = 'chunked', embedding_config_id = NULL, "
        "embedded_chunk_count = NULL, updated_at = now() - interval '1 hour' WHERE id = :d",
        d=doc_id,
    )
    create_celery_app(client.app.state.settings, configure_logs=False).set_current()  # type: ignore[attr-defined]

    report = recover_stalled_embeddings.apply().get()

    assert report["requeued"] >= 1
    assert wait_for(client, alice, doc_id, {"ready", "failed"})["status"] == "ready"
    assert len(vectors_of(migrated_database_url, doc_id)) > 0


def test_a_broker_outage_after_chunking_does_not_fail_the_document(
    pipeline_client_factory: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import document_tasks

    original: Callable[..., Any] = Celery.send_task

    def refuse_embedding(self: Celery, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == EMBED_DOCUMENT_TASK:
            raise ConnectionError("broker down")
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(Celery, "send_task", refuse_embedding)
    client = pipeline_client_factory()
    alice = make_user(client, "alice@example.com")

    doc_id = _upload(client, alice, "n.txt", b"broker outage after chunking " * 5)
    done = wait_for(client, alice, doc_id, {"chunked", "failed"})

    assert done["status"] == "chunked" and done["failure_reason"] is None
    assert document_tasks._queue_embedding is not None


def test_a_time_limit_requeues_the_embedding_immediately(
    auth_client: TestClient, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from celery.exceptions import SoftTimeLimitExceeded

    from app.workers import embedding_tasks
    from app.workers.celery_app import create_celery_app

    alice = make_user(auth_client, "alice@example.com")
    doc_id = str(
        upload_ok(auth_client, alice, filename="n.txt", content=b"slow " * 40, content_type=None)[
            "id"
        ]
    )
    process_now(auth_client, doc_id)
    db_execute(
        migrated_database_url,
        "UPDATE documents SET status='embedding', embedding_attempts=1, "
        "embedding_updated_at=now() WHERE id=:d",
        d=doc_id,
    )

    async def interrupted(*args: object, **kwargs: object) -> None:
        raise SoftTimeLimitExceeded

    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(embedding_tasks, "_embed", interrupted)
    monkeypatch.setattr(Celery, "send_task", lambda self, name, *a, **kw: sent.append((name, kw)))
    create_celery_app(auth_client.app.state.settings, configure_logs=False).set_current()  # type: ignore[attr-defined]

    result = embedding_tasks.embed_document.apply(args=[doc_id]).get()

    assert result == {"status": "retry", "reason": "timeout"}
    assert sent and sent[0][0] == EMBED_DOCUMENT_TASK and sent[0][1]["ignore_result"] is True


def test_a_time_budget_continues_in_a_new_task_until_the_document_is_ready(
    embedding_pipeline_client_factory: ClientFactory, migrated_database_url: str
) -> None:
    from tests.embedding_fakes import SlowProvider

    register_provider("test_budget_worker", partial(SlowProvider, delay=0.4))
    client = embedding_pipeline_client_factory(
        embedding_provider="test_budget_worker",
        chunking_max_chars=200,
        chunking_overlap_chars=20,
        embedding_batch_size=2,
    )
    alice = make_user(client, "alice@example.com")
    body = json.dumps([f"Sentence number {i} about the topic. " * 3 for i in range(10)])

    doc_id = _upload(client, alice, "long.txt", body.encode())
    done = wait_for(client, alice, doc_id, {"ready", "failed"}, timeout=90)

    assert done["status"] == "ready" and done["embedded_chunk_count"] == done["chunk_count"]
