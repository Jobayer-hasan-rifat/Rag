"""Helpers for the document-version tests."""

from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.helpers import db_rows
from tests.processing_helpers import embed_now, process_now

DOCS = "/api/v1/documents"


def upload_version(
    client: TestClient,
    headers: dict[str, str] | None,
    document_id: str,
    content: str | bytes,
    name: str = "v.txt",
) -> Response:
    data = content.encode() if isinstance(content, str) else content
    return client.post(
        f"{DOCS}/{document_id}/versions", headers=headers or {}, files={"file": (name, data, "")}
    )


def new_version(
    client: TestClient,
    headers: dict[str, str],
    document_id: str,
    content: str | bytes,
    name: str = "v.txt",
) -> str:
    """Upload a replacement and return its version id (not processed yet)."""
    response = upload_version(client, headers, document_id, content, name)
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["version"]["id"])


def ready_version(
    client: TestClient,
    headers: dict[str, str],
    document_id: str,
    content: str | bytes,
    name: str = "v.txt",
) -> str:
    """Upload a replacement and run it through processing and embedding."""
    version_id = new_version(client, headers, document_id, content, name)
    assert process_now(client, version_id).status == "chunked"
    assert embed_now(client, version_id).status == "ready"
    return version_id


def versions_api(client: TestClient, headers: dict[str, str], document_id: str) -> list[Any]:
    response = client.get(f"{DOCS}/{document_id}/versions", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["data"])


def version_rows(db_url: str, document_id: str) -> list[dict[str, Any]]:
    """(version_number, status, is_active, is_current) of every version, oldest first."""
    rows = db_rows(
        db_url,
        "SELECT id::text, version_number, status, is_active, is_current FROM documents "
        "WHERE family_id = :f ORDER BY version_number",
        f=document_id,
    )
    return [
        {"id": r[0], "number": r[1], "status": r[2], "active": r[3], "current": r[4]} for r in rows
    ]


def active_id(db_url: str, document_id: str) -> str | None:
    rows = [r for r in version_rows(db_url, document_id) if r["active"]]
    assert len(rows) <= 1
    return rows[0]["id"] if rows else None


def search_names(client: TestClient, headers: dict[str, str], query: str, **body: Any) -> list[str]:
    response = client.post(
        "/api/v1/search/hybrid", headers=headers, json={"query": query, "top_k": 10, **body}
    )
    assert response.status_code == 200, response.text
    return [item["text"] for item in response.json()["data"]["results"]]
