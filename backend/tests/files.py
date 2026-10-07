import io
import zipfile
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.helpers import bearer, register_and_login

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def pdf_bytes(marker: str = "one") -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        + f"% {marker}\n".encode()
        + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def docx_bytes(marker: str = "one") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<w:document>{marker}</w:document>")
    return buffer.getvalue()


def text_bytes(marker: str = "one") -> bytes:
    return f"Plain text document {marker}\n".encode()


def markdown_bytes(marker: str = "one") -> bytes:
    return f"# Title {marker}\n\nSome *markdown*.\n".encode()


def make_user(client: TestClient, email: str) -> dict[str, str]:
    """Register, log in, and return ready-to-use authorization headers."""
    return bearer(register_and_login(client, email=email)["access_token"])


def upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    filename: str = "report.pdf",
    content: bytes | None = None,
    content_type: str | None = "application/pdf",
    collection_ids: list[str] | None = None,
) -> Response:
    files: dict[str, Any] = {
        "file": (filename, content if content is not None else pdf_bytes(), content_type or "")
    }
    data = {}
    if collection_ids is not None:
        import json

        data["collection_ids"] = json.dumps(collection_ids)
    response: Response = client.post("/api/v1/documents", headers=headers, files=files, data=data)
    return response


def upload_ok(client: TestClient, headers: dict[str, str], **kwargs: Any) -> dict[str, Any]:
    response = upload(client, headers, **kwargs)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def create_collection(client: TestClient, headers: dict[str, str], name: str = "Papers") -> str:
    response = client.post("/api/v1/collections", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])
