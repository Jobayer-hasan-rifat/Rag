import asyncio
import time
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.services.processing_service import ProcessingOutcome
from app.workers.document_tasks import _process
from tests.helpers import bearer, db_rows

DOCS = "/api/v1/documents"


def process_now(
    client: TestClient, document_id: str, task_id: str = "test-task"
) -> ProcessingOutcome:
    """Run the worker-side pipeline for one document in this process (no broker involved)."""
    settings = client.app.state.settings  # type: ignore[attr-defined]
    return asyncio.run(_process(settings, uuid.UUID(document_id), task_id))


def fetch_document(client: TestClient, headers: dict[str, str], document_id: str) -> dict[str, Any]:
    response = client.get(f"{DOCS}/{document_id}", headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def wait_for_status(
    client: TestClient,
    headers: dict[str, str],
    document_id: str,
    wanted: set[str],
    timeout: float = 40.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = fetch_document(client, headers, document_id)
        if data["status"] in wanted:
            return data
        time.sleep(0.2)
    raise AssertionError(f"document stayed in {data['status']!r}, wanted {wanted}")


def sections_of(db_url: str, document_id: str) -> list[dict[str, Any]]:
    rows = db_rows(
        db_url,
        "SELECT ordinal, kind, page_number, heading, heading_level, text, char_count "
        "FROM document_sections WHERE document_id = :d ORDER BY ordinal",
        d=document_id,
    )
    names = ("ordinal", "kind", "page_number", "heading", "heading_level", "text", "char_count")
    return [dict(zip(names, row, strict=True)) for row in rows]


def row_of(db_url: str, document_id: str) -> dict[str, Any]:
    rows = db_rows(
        db_url,
        "SELECT status, failure_reason, error_message, page_count, character_count, "
        "processing_attempts, processing_started_at, processing_completed_at, processing_metadata "
        "FROM documents WHERE id = :d",
        d=document_id,
    )
    names = (
        "status", "failure_reason", "error_message", "page_count", "character_count",
        "processing_attempts", "processing_started_at", "processing_completed_at", "metadata",
    )  # fmt: skip
    return dict(zip(names, rows[0], strict=True))


def chunks_of(db_url: str, document_id: str) -> list[dict[str, Any]]:
    rows = db_rows(
        db_url,
        "SELECT c.chunk_index, c.text, c.char_count, c.text_sha256, c.start_char, c.end_char, "
        "c.overlap_chars, c.page_number, c.heading, c.heading_level, c.heading_path, "
        "c.chunking_version, s.ordinal, s.text "
        "FROM document_chunks c JOIN document_sections s ON s.id = c.section_id "
        "WHERE c.document_id = :d ORDER BY c.chunk_index",
        d=document_id,
    )
    names = (
        "chunk_index", "text", "char_count", "text_sha256", "start_char", "end_char",
        "overlap_chars", "page_number", "heading", "heading_level", "heading_path",
        "chunking_version", "section_ordinal", "section_text",
    )  # fmt: skip
    return [dict(zip(names, row, strict=True)) for row in rows]


def rechunk_now(
    client: TestClient, document_id: str, task_id: str = "test-rechunk", **overrides: Any
) -> ProcessingOutcome:
    """Run the re-chunking path in this process, optionally with changed chunking settings."""
    from app.workers.document_tasks import _rechunk

    settings = client.app.state.settings  # type: ignore[attr-defined]
    if overrides:
        settings = settings.model_copy(update=overrides)
    return asyncio.run(_rechunk(settings, uuid.UUID(document_id), task_id))


__all__ = ["bearer"]
