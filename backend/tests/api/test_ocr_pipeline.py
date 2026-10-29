"""A scanned PDF through the real processing pipeline (upload, worker, sections, chunks), with a
deterministic stand-in for Tesseract so the test does not depend on the program being installed."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.parsers.ocr import OcrEngine, set_engine_factory
from tests.conftest import ClientFactory
from tests.files import make_user, upload_ok
from tests.helpers import db_scalar
from tests.pdf_factory import mixed_pdf
from tests.processing_helpers import fetch_document, process_now, row_of, sections_of

pytestmark = pytest.mark.integration


class PageText(OcrEngine):
    def __init__(self) -> None:
        self.calls = 0

    def recognise(self, image_png: bytes) -> str:
        self.calls += 1
        return f"Scanned page {self.calls}: the brood-fish ban lasts 22 consecutive days."


@pytest.fixture
def ocr_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[PageText]:
    engine = PageText()
    monkeypatch.setattr("app.parsers.pdf_parser.tesseract_available", lambda config: True)
    set_engine_factory(lambda config: engine)
    yield engine
    set_engine_factory(None)


def _upload_scan(client: TestClient) -> tuple[dict[str, str], str]:
    alice = make_user(client, "alice@example.com")
    data = mixed_pdf("scan:page one", "text:A page that already has a text layer of its own.")
    doc = upload_ok(
        client, alice, filename="scan.pdf", content=data, content_type="application/pdf"
    )
    return alice, str(doc["id"])


def test_a_scanned_pdf_becomes_searchable_text_when_ocr_is_on(
    auth_client_factory: ClientFactory, ocr_engine: PageText, migrated_database_url: str
) -> None:
    client = auth_client_factory(ocr_enabled=True)
    alice, document_id = _upload_scan(client)

    outcome = process_now(client, document_id)

    assert outcome.status == "chunked"
    sections = sections_of(migrated_database_url, document_id)
    assert [s["page_number"] for s in sections] == [1, 2]
    assert "brood-fish ban lasts 22 consecutive days" in sections[0]["text"]
    assert sections[1]["text"].startswith("A page that already has a text layer")
    assert ocr_engine.calls == 1  # the page with its own text was not recognised again
    metadata = db_scalar(
        migrated_database_url,
        "SELECT processing_metadata->'properties'->>'ocr_pages' FROM documents WHERE id = :d",
        d=document_id,
    )
    assert metadata == "1"
    assert fetch_document(client, alice, document_id)["status"] == "chunked"


def test_the_same_scan_still_fails_clearly_when_ocr_is_off(
    auth_client_factory: ClientFactory, ocr_engine: PageText, migrated_database_url: str
) -> None:
    client = auth_client_factory(ocr_enabled=False)
    alice = make_user(client, "alice@example.com")
    data = mixed_pdf("scan:page one")
    doc = upload_ok(
        client, alice, filename="scan.pdf", content=data, content_type="application/pdf"
    )

    outcome = process_now(client, str(doc["id"]))

    assert (outcome.status, outcome.reason) == ("failed", "empty_document")
    assert ocr_engine.calls == 0
    assert row_of(migrated_database_url, str(doc["id"]))["failure_reason"] == "empty_document"
    message = fetch_document(client, alice, str(doc["id"]))["error_message"]
    assert "OCR" in message and "turned off" in message
