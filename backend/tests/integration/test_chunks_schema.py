import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from tests.helpers import (
    create_user_in_db,
    db_execute,
    db_rows,
    db_scalar,
    downgrade,
    upgrade,
)

pytestmark = pytest.mark.integration

INSERT_DOC = (
    "INSERT INTO documents (id, user_id, filename, storage_key, file_type, content_type, "
    "file_size, checksum_sha256) VALUES (:id, :u, 'f.txt', :k, 'txt', 'text/plain', 10, :c)"
)
INSERT_SECTION = (
    "INSERT INTO document_sections (id, document_id, ordinal, kind, page_number, text, char_count) "
    "VALUES (:s, :d, :o, 'page', :p, :t, :n)"
)
INSERT_CHUNK = (
    "INSERT INTO document_chunks (document_id, section_id, chunking_version, chunk_index, text, "
    "char_count, text_sha256, start_char, end_char, overlap_chars, page_number, heading_level) "
    "VALUES (:d, :s, 'c1.0', :i, :t, :n, :h, :a, :b, :ov, :p, :hl)"
)


def _document(url: str, user_id: str) -> str:
    doc_id = str(uuid.uuid4())
    db_execute(
        url,
        INSERT_DOC,
        id=doc_id,
        u=user_id,
        k=f"documents/aa/{uuid.uuid4().hex}",
        c=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    return doc_id


def _section(url: str, doc: str, ordinal: int = 0, text: str = "0123456789") -> str:
    section_id = str(uuid.uuid4())
    db_execute(
        url, INSERT_SECTION, s=section_id, d=doc, o=ordinal, p=ordinal + 1, t=text, n=len(text)
    )
    return section_id


def _chunk(url: str, doc: str, section: str, **kwargs: Any) -> None:
    text = kwargs.pop("text", "01234")
    values: dict[str, Any] = {
        "i": 0, "a": 0, "b": len(text), "ov": 0, "p": 1, "hl": None, "n": len(text),
    }  # fmt: skip
    values.update(kwargs)
    db_execute(url, INSERT_CHUNK, d=doc, s=section, t=text, h="0" * 64, **values)


@pytest.fixture
def seeded(migrated_database_url: str) -> tuple[str, str, str]:
    user = create_user_in_db(migrated_database_url, email=f"{uuid.uuid4().hex[:8]}@example.com")
    doc = _document(migrated_database_url, user)
    return migrated_database_url, doc, _section(migrated_database_url, doc)


def test_valid_chunk_is_stored_with_defaults(seeded: tuple[str, str, str]) -> None:
    url, doc, section = seeded

    _chunk(url, doc, section, text="বাংলা পাঠ")

    row = db_rows(
        url, "SELECT overlap_chars, heading_path::text, chunking_version FROM document_chunks"
    )[0]
    assert tuple(row) == (0, "{}", "c1.0")


@pytest.mark.parametrize(
    ("kwargs", "constraint"),
    [
        ({"n": 99}, "char_count_matches"),
        ({"i": -1}, "index_non_negative"),
        ({"a": 3, "b": 3, "text": ""}, "not_empty|char_count_matches|span_valid"),
        ({"b": 99}, "span_matches_length"),
        ({"a": -1, "b": 4}, "span_valid|span_matches_length"),
        ({"ov": 6}, "overlap_valid"),
        ({"ov": -1}, "overlap_valid"),
        ({"p": 0}, "page_number_positive"),
        ({"hl": 10}, "heading_level_range"),
    ],
)
def test_chunk_constraints_reject_bad_rows(
    seeded: tuple[str, str, str], kwargs: dict[str, Any], constraint: str
) -> None:
    url, doc, section = seeded

    with pytest.raises(IntegrityError, match=constraint):
        _chunk(url, doc, section, **kwargs)


def test_chunk_order_is_unique_per_document_and_version(seeded: tuple[str, str, str]) -> None:
    url, doc, section = seeded
    _chunk(url, doc, section, i=0)

    with pytest.raises(IntegrityError, match="uq_document_chunks_order"):
        _chunk(url, doc, section, i=0)


def test_a_chunk_cannot_reference_another_documents_section(seeded: tuple[str, str, str]) -> None:
    url, doc, _ = seeded
    other_user = create_user_in_db(url, email="other@example.com")
    other_doc = _document(url, other_user)
    foreign_section = _section(url, other_doc)

    with pytest.raises(IntegrityError, match="fk_document_chunks_section"):
        _chunk(url, doc, foreign_section)


def test_deleting_a_document_cascades_to_sections_and_chunks(seeded: tuple[str, str, str]) -> None:
    url, doc, section = seeded
    _chunk(url, doc, section)

    db_execute(url, "DELETE FROM documents WHERE id = :d", d=doc)

    assert db_scalar(url, "SELECT count(*) FROM document_chunks WHERE document_id = :d", d=doc) == 0
    assert (
        db_scalar(url, "SELECT count(*) FROM document_sections WHERE document_id = :d", d=doc) == 0
    )


def test_deleting_a_section_cascades_to_its_chunks(seeded: tuple[str, str, str]) -> None:
    url, doc, section = seeded
    _chunk(url, doc, section)

    db_execute(url, "DELETE FROM document_sections WHERE id = :s", s=section)

    assert db_scalar(url, "SELECT count(*) FROM document_chunks WHERE document_id = :d", d=doc) == 0


def test_chunked_is_a_valid_document_status_and_counts_cannot_be_negative(
    seeded: tuple[str, str, str],
) -> None:
    url, doc, _ = seeded
    db_execute(
        url,
        "UPDATE documents SET status = 'chunked', chunk_count = 3, chunking_version = 'c1.0' "
        "WHERE id = :d",
        d=doc,
    )
    with pytest.raises(IntegrityError, match="chunk_count_non_negative"):
        db_execute(url, "UPDATE documents SET chunk_count = -1 WHERE id = :d", d=doc)
    with pytest.raises(IntegrityError, match="status_valid"):
        db_execute(url, "UPDATE documents SET status = 'indexed' WHERE id = :d", d=doc)


def test_migration_0005_sends_phase4_ready_documents_back_to_pending(
    seeded: tuple[str, str, str],
) -> None:
    url, doc, _ = seeded
    downgrade(url, "0004")
    db_execute(
        url,
        "UPDATE documents SET status = 'ready', processing_attempts = 2 WHERE id = :d",
        d=doc,
    )

    upgrade(url)

    row = db_rows(url, "SELECT status, processing_attempts FROM documents WHERE id = :d", d=doc)[0]
    assert tuple(row) == ("pending", 0)


def test_migration_0005_downgrade_removes_chunks_and_restores_old_statuses(
    seeded: tuple[str, str, str],
) -> None:
    url, doc, section = seeded
    _chunk(url, doc, section)
    db_execute(url, "UPDATE documents SET status = 'chunked' WHERE id = :d", d=doc)

    downgrade(url, "0004")

    assert db_scalar(url, "SELECT to_regclass('document_chunks')") is None
    assert db_scalar(url, "SELECT status FROM documents WHERE id = :d", d=doc) == "ready"
    columns = {
        r[0]
        for r in db_rows(
            url,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'",
        )
    }
    assert not {"chunk_count", "chunking_version"} & columns

    upgrade(url)

    assert db_scalar(url, "SELECT to_regclass('document_chunks')") is not None
