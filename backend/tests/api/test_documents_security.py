import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.observability.logging import JsonFormatter, RequestContextFilter
from tests.conftest import ClientFactory
from tests.files import make_user, pdf_bytes, text_bytes, upload, upload_ok
from tests.helpers import db_rows

pytestmark = pytest.mark.integration

KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _stored(client: TestClient) -> list[Path]:
    root = Path(client.app.state.settings.storage_local_path)  # type: ignore[attr-defined]
    return [p for p in root.rglob("*") if p.is_file()]


TRAVERSAL_NAMES = [
    "../../../etc/passwd.pdf",
    "..\\..\\..\\windows\\system32\\config\\sam.pdf",
    "/etc/shadow.pdf",
    "C:\\Windows\\win.pdf",
    "\\\\server\\share\\file.pdf",
    "....//....//escape.pdf",
    "%2e%2e%2f%2e%2e%2fescape.pdf",
    "..%5c..%5cescape.pdf",
    "file.pdf\x00.exe",
    "nul.pdf",
    "CON.pdf",
    "a" * 300 + ".pdf",
    ".htaccess.pdf",
    "e\u0301\u0301.pdf",
    "\u202egnp.pdf",
    "../" * 40 + "deep.pdf",
]


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_malicious_filenames_never_influence_where_files_are_stored(
    auth_client: TestClient, tmp_path: Path, name: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = upload(auth_client, alice, filename=name, content=pdf_bytes(name))

    assert response.status_code in (201, 415, 422)
    storage_root = tmp_path / "storage"
    everything = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert all(storage_root in p.parents for p in everything), "a file was written outside storage"
    for path in _stored(auth_client):
        assert KEY_PATTERN.match(path.name), path
        assert path.parent.name == path.name[:2]
    if response.status_code == 201:
        shown = response.json()["data"]["filename"]
        assert "/" not in shown and "\\" not in shown and "\x00" not in shown
        assert ".." not in shown.split(".pdf")[0] or "/" not in shown


def test_traversal_attempts_do_not_create_files_beside_the_storage_root(
    auth_client: TestClient, tmp_path: Path
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    for index, name in enumerate(TRAVERSAL_NAMES[:6]):
        upload(auth_client, alice, filename=name, content=pdf_bytes(f"t{index}"))

    assert {p.name for p in tmp_path.iterdir()} == {"storage"}
    assert not (tmp_path.parent / "escape.pdf").exists()


def test_stored_names_are_independent_of_each_upload_name(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    upload_ok(auth_client, alice, filename="same.pdf", content=pdf_bytes("a"))
    upload_ok(auth_client, alice, filename="same.pdf", content=pdf_bytes("b"))

    names = [p.name for p in _stored(auth_client)]

    assert len(set(names)) == 2
    assert all("same" not in n for n in names)


def test_storage_keys_are_unique_and_not_derived_from_content_or_owner(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    for index in range(5):
        upload_ok(auth_client, alice, filename=f"f{index}.pdf", content=pdf_bytes(str(index)))

    rows = db_rows(
        migrated_database_url, "SELECT storage_key, checksum_sha256, user_id::text FROM documents"
    )

    keys = [r[0] for r in rows]
    assert len(set(keys)) == 5
    for key, checksum, user_id in rows:
        assert checksum not in key and user_id not in key


def test_double_extension_and_active_content_are_rejected_or_neutralised(
    auth_client: TestClient,
) -> None:
    alice = make_user(auth_client, "alice@example.com")

    script = upload(
        auth_client,
        alice,
        filename="shell.php",
        content=b"<?php system($_GET['c']); ?>",
        content_type="text/plain",
    )
    html_as_txt = upload(
        auth_client,
        alice,
        filename="x.txt",
        content=b"<script>alert(1)</script>",
        content_type="text/plain",
    )

    assert script.status_code == 415
    assert (
        html_as_txt.status_code == 201
    )  # plain text is stored inert and always served as an attachment
    doc_id = html_as_txt.json()["data"]["id"]
    download = auth_client.get(f"/api/v1/documents/{doc_id}/download", headers=alice)
    assert download.headers["content-type"].startswith("text/plain")
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.headers["x-content-type-options"] == "nosniff"


def test_uploaded_files_are_not_executable(auth_client: TestClient) -> None:
    import os
    import stat

    alice = make_user(auth_client, "alice@example.com")
    upload_ok(auth_client, alice)

    if os.name == "posix":
        mode = _stored(auth_client)[0].stat().st_mode
        assert not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.mark.parametrize(
    "path", ["/uploads/x", "/static/x", "/files/x", "/storage/x", "/data/uploads/x", "/documents/x"]
)
def test_stored_files_are_not_served_publicly(auth_client: TestClient, path: str) -> None:
    alice = make_user(auth_client, "alice@example.com")
    upload_ok(auth_client, alice)

    for headers in ({}, alice):
        response = auth_client.get(path, headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_application_exposes_no_static_file_mounts(auth_client: TestClient) -> None:
    from starlette.routing import Mount

    assert not [r for r in auth_client.app.routes if isinstance(r, Mount)]  # type: ignore[attr-defined]


def test_too_many_file_parts_are_rejected(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    response = auth_client.post(
        "/api/v1/documents",
        headers=alice,
        files=[
            ("file", ("a.pdf", pdf_bytes("a"), "application/pdf")),
            ("file", ("b.pdf", pdf_bytes("b"), "application/pdf")),
        ],
    )

    assert response.status_code == 400
    assert _stored(auth_client) == []


def test_oversized_streamed_body_without_content_length_is_rejected(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(max_upload_bytes=2048)
    alice = make_user(client, "alice@example.com")
    boundary = "xxBOUNDARYxx"

    def body() -> Iterator[bytes]:
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        for _ in range(100):
            yield b"a" * 1024
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/api/v1/documents",
        headers={**alice, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        content=body(),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert _stored(client) == []


def test_oversized_declared_length_is_rejected_before_any_parsing(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(max_upload_bytes=2048)
    alice = make_user(client, "alice@example.com")
    huge = b"x" * (2048 + 1024 * 1024 + 10)

    response = client.post(
        "/api/v1/documents",
        headers={**alice, "Content-Type": "multipart/form-data; boundary=zz"},
        content=huge,
    )

    assert response.status_code == 413
    assert response.json()["error"]["details"] == {"max_bytes": 2048}
    assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]


def test_rate_limited_clients_are_rejected_before_the_body_is_read(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rate_limit_upload_attempts=1)
    alice = make_user(client, "alice@example.com")
    upload_ok(client, alice)

    response = client.post(
        "/api/v1/documents",
        headers={**alice, "Content-Type": "multipart/form-data; boundary=zz"},
        content=b"not even valid multipart",
    )

    assert response.status_code == 429  # would be 4xx parse error if the body had been read first


def test_filenames_and_contents_never_reach_the_logs(auth_client_factory: ClientFactory) -> None:
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    client = auth_client_factory(log_level="DEBUG")
    handler = Capture(level=logging.DEBUG)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter("test"))
    logging.getLogger().addHandler(handler)
    try:
        alice = make_user(client, "alice@example.com")
        doc = upload_ok(
            client, alice, filename="TOPSECRET-name.txt",
            content=b"CONFIDENTIAL-CONTENT-123", content_type="text/plain",
        )  # fmt: skip
        client.get(f"/api/v1/documents/{doc['id']}/download", headers=alice)
        client.delete(f"/api/v1/documents/{doc['id']}", headers=alice)
        upload(client, alice, filename="TOPSECRET-bad.exe", content=b"MZ")
    finally:
        logging.getLogger().removeHandler(handler)

    logged = "\n".join(lines)
    assert "document uploaded" in logged and "document deleted" in logged
    for secret in ("TOPSECRET", "CONFIDENTIAL-CONTENT", "storage_key"):
        assert secret not in logged
    assert not re.search(r"documents/[0-9a-f]{2}/[0-9a-f]{32}", logged)  # no storage keys


def test_errors_never_leak_filesystem_or_infrastructure_details(
    auth_client: TestClient, tmp_path: Path
) -> None:
    alice = make_user(auth_client, "alice@example.com")
    responses = [
        upload(auth_client, alice, filename="x.exe", content=b"MZ"),
        upload(auth_client, alice, filename="x.pdf", content=b"nope"),
        auth_client.get(f"/api/v1/documents/{'0' * 8}-0000-0000-0000-{'0' * 12}", headers=alice),
        auth_client.get("/api/v1/documents/not-a-uuid", headers=alice),
    ]

    for response in responses:
        for leak in (
            str(tmp_path),
            "Traceback",
            "postgres",
            "asyncpg",
            "sqlalchemy",
            "storage_key",
            "site-packages",
        ):
            assert leak not in response.text


def test_text_uploads_are_validated_as_utf8_text_not_binary(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    ok = upload(
        auth_client,
        alice,
        filename="ok.txt",
        content=text_bytes("ünïcode ✓"),
        content_type="text/plain",
    )
    binary = upload(
        auth_client, alice, filename="bin.txt", content=bytes(range(256)), content_type="text/plain"
    )

    assert ok.status_code == 201
    assert binary.status_code == 415
