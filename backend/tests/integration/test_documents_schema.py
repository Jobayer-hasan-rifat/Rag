import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.documents.lifecycle import DocumentStatus
from app.db.repositories.chunk_repository import ChunkRepository
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.exceptions import InvalidStatusTransitionError
from app.models.document import Document
from app.services.document_service import DocumentService
from app.storage.local import LocalStorageProvider
from tests.conftest import SettingsFactory
from tests.helpers import (
    RecordingQueue,
    create_user_in_db,
    db_autocommit,
    db_execute,
    db_rows,
    db_scalar,
    downgrade,
    upgrade,
)

pytestmark = pytest.mark.integration

INSERT_DOC = (
    "INSERT INTO documents (id, user_id, filename, storage_key, file_type, content_type, "
    "file_size, checksum_sha256) VALUES (:id, :u, 'f.pdf', :k, :t, 'application/pdf', :s, :c)"
)


@pytest.fixture
def database_url(postgres_url: str) -> Iterator[str]:
    name = f"docs_{uuid.uuid4().hex[:10]}"
    db_autocommit(postgres_url, f'CREATE DATABASE "{name}"')
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    upgrade(url)
    yield url
    db_autocommit(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _insert_document(
    url: str, user_id: str, *, key: str | None = None, file_type: str = "pdf", size: int = 10,
    checksum: str | None = None, status: str | None = None, error: str | None = None,
) -> str:  # fmt: skip
    doc_id = str(uuid.uuid4())
    db_execute(
        url,
        INSERT_DOC,
        id=doc_id,
        u=user_id,
        k=key or f"documents/aa/{uuid.uuid4().hex}",
        t=file_type,
        s=size,
        c=checksum or uuid.uuid4().hex + uuid.uuid4().hex,
    )
    if status:
        db_execute(
            url,
            "UPDATE documents SET status = :s, error_message = :e WHERE id = :i",
            s=status,
            e=error,
            i=doc_id,
        )
    return doc_id


def _insert_collection(url: str, user_id: str, name: str = "C") -> str:
    cid = str(uuid.uuid4())
    db_execute(
        url,
        "INSERT INTO collections (id, user_id, name) VALUES (:i, :u, :n)",
        i=cid,
        u=user_id,
        n=name,
    )
    return cid


def _link(url: str, doc: str, collection: str, user: str) -> None:
    db_execute(
        url,
        "INSERT INTO document_collections (document_id, collection_id, user_id) "
        "VALUES (:d, :c, :u)",
        d=doc, c=collection, u=user,
    )  # fmt: skip


async def _inspect(url: str, fn):  # type: ignore[no-untyped-def]
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(fn)
    finally:
        await engine.dispose()


def _names(connection: Connection) -> dict[str, set[str]]:
    insp = inspect(connection)
    return {
        table: {str(i["name"]) for i in insp.get_indexes(table)}
        | {str(c["name"]) for c in insp.get_unique_constraints(table)}
        for table in ("collections", "documents", "document_collections")
    }


def test_migration_cycle_up_down_up_keeps_earlier_tables(database_url: str) -> None:
    downgrade(database_url, "0002")
    tables = asyncio.run(_inspect(database_url, lambda c: set(inspect(c).get_table_names())))
    assert {"users", "roles", "refresh_tokens"} <= tables
    assert not {"documents", "collections", "document_collections"} & tables

    upgrade(database_url)

    tables = asyncio.run(_inspect(database_url, lambda c: set(inspect(c).get_table_names())))
    assert {"documents", "collections", "document_collections"} <= tables


def test_expected_indexes_and_unique_constraints_exist(database_url: str) -> None:
    found = asyncio.run(_inspect(database_url, _names))

    assert {
        "uq_collections_id_user_id",
        "ix_collections_user_id",
        "uq_collections_user_name",
    } <= found["collections"]
    assert {
        "uq_documents_storage_key", "uq_documents_user_checksum", "uq_documents_id_user_id",
        "ix_documents_user_created", "ix_documents_status",
    } <= found["documents"]  # fmt: skip
    assert "ix_document_collections_collection_id" in found["document_collections"]


@pytest.mark.parametrize(
    ("kwargs", "constraint"),
    [
        ({"file_type": "exe"}, "file_type_valid"),
        ({"size": 0}, "file_size_positive"),
        ({"size": -5}, "file_size_positive"),
        ({"status": "deleted"}, "status_valid"),
        ({"status": "ready", "error": "oops"}, "error_only_when_failed"),
    ],
)
def test_check_constraints_reject_invalid_documents(
    database_url: str, kwargs: dict[str, object], constraint: str
) -> None:
    user = create_user_in_db(database_url, email="u@example.com")

    with pytest.raises(IntegrityError, match=constraint):
        _insert_document(database_url, user, **kwargs)  # type: ignore[arg-type]


def test_failed_documents_may_carry_an_error_message(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")

    _insert_document(database_url, user, status="failed", error="parse error")

    assert db_scalar(database_url, "SELECT error_message FROM documents") == "parse error"


def test_storage_key_and_per_user_checksum_are_unique(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    bob = create_user_in_db(database_url, email="b@example.com")
    _insert_document(database_url, alice, key="documents/aa/" + "1" * 32, checksum="c" * 64)

    with pytest.raises(IntegrityError, match="uq_documents_storage_key"):
        _insert_document(database_url, bob, key="documents/aa/" + "1" * 32)
    with pytest.raises(IntegrityError, match="uq_documents_user_checksum"):
        _insert_document(database_url, alice, checksum="c" * 64)
    _insert_document(database_url, bob, checksum="c" * 64)  # same content for another user is fine


def test_collection_names_are_unique_per_user_ignoring_case(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    bob = create_user_in_db(database_url, email="b@example.com")
    _insert_collection(database_url, alice, "Papers")

    with pytest.raises(IntegrityError, match="uq_collections_user_name"):
        _insert_collection(database_url, alice, "PAPERS")
    _insert_collection(database_url, bob, "Papers")


def test_a_document_cannot_be_linked_to_another_users_collection(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    bob = create_user_in_db(database_url, email="b@example.com")
    alices_doc = _insert_document(database_url, alice)
    bobs_collection = _insert_collection(database_url, bob)

    for owner in (alice, bob):
        with pytest.raises(IntegrityError, match="fk_document_collections"):
            _link(database_url, alices_doc, bobs_collection, owner)


def test_a_same_owner_link_is_accepted(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    doc = _insert_document(database_url, alice)
    collection = _insert_collection(database_url, alice)

    _link(database_url, doc, collection, alice)

    assert db_scalar(database_url, "SELECT count(*) FROM document_collections") == 1


def test_deletion_cascades_follow_the_documented_behaviour(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    doc = _insert_document(database_url, alice)
    keep = _insert_document(database_url, alice)
    collection = _insert_collection(database_url, alice)
    _link(database_url, doc, collection, alice)
    _link(database_url, keep, collection, alice)

    db_execute(database_url, "DELETE FROM documents WHERE id = :i", i=doc)
    assert db_scalar(database_url, "SELECT count(*) FROM document_collections") == 1

    db_execute(database_url, "DELETE FROM collections WHERE id = :i", i=collection)
    assert db_scalar(database_url, "SELECT count(*) FROM document_collections") == 0
    assert db_scalar(database_url, "SELECT count(*) FROM documents") == 1  # documents survive

    db_execute(database_url, "DELETE FROM users WHERE id = :i", i=alice)
    assert db_scalar(database_url, "SELECT count(*) FROM documents") == 0


def test_timestamps_and_status_default(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    _insert_document(database_url, user)

    row = db_rows(
        database_url, "SELECT status, created_at IS NOT NULL, updated_at IS NOT NULL FROM documents"
    )[0]

    assert tuple(row) == ("pending", True, True)


# --- lifecycle through the service, persisted in real PostgreSQL ---------------------------


@pytest.fixture
def seeded_document(database_url: str) -> str:
    user = create_user_in_db(database_url, email="life@example.com")
    return _insert_document(database_url, user)


@pytest.fixture
async def lifecycle_env(
    database_url: str,
    seeded_document: str,
    make_settings: SettingsFactory,
    tmp_path: Path,
) -> AsyncIterator[tuple[DocumentService, Document, Settings]]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    settings = make_settings(database_url=database_url, storage_local_path=str(tmp_path / "s"))
    engine = create_async_engine(database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        document = await session.get(Document, uuid.UUID(seeded_document))
        assert document is not None
        service = DocumentService(
            session=session,
            documents=DocumentRepository(session),
            collections=CollectionRepository(session),
            chunks=ChunkRepository(session),
            storage=LocalStorageProvider(tmp_path / "s"),
            processing_queue=RecordingQueue(),
            settings=settings,
        )
        yield service, document, settings
    await engine.dispose()


async def test_status_moves_through_the_pipeline_and_is_persisted(
    lifecycle_env: tuple[DocumentService, Document, Settings], database_url: str
) -> None:
    service, document, _ = lifecycle_env

    for stage in (DocumentStatus.PARSING, DocumentStatus.CHUNKING, DocumentStatus.CHUNKED,
                  DocumentStatus.EMBEDDING, DocumentStatus.INDEXING,
                  DocumentStatus.READY):  # fmt: skip
        await service.change_status(document, stage)

    assert (
        await asyncio.to_thread(db_scalar, database_url, "SELECT status FROM documents") == "ready"
    )


async def test_invalid_transition_is_rejected_and_leaves_the_row_unchanged(
    lifecycle_env: tuple[DocumentService, Document, Settings], database_url: str
) -> None:
    service, document, _ = lifecycle_env

    with pytest.raises(InvalidStatusTransitionError):
        await service.change_status(document, DocumentStatus.READY)

    assert document.status == "pending"
    assert (
        await asyncio.to_thread(db_scalar, database_url, "SELECT status FROM documents")
        == "pending"
    )


async def test_failure_records_the_error_and_requeue_clears_it(
    lifecycle_env: tuple[DocumentService, Document, Settings], database_url: str
) -> None:
    service, document, _ = lifecycle_env

    await service.change_status(document, DocumentStatus.PARSING)
    await service.change_status(document, DocumentStatus.FAILED, error_message="unreadable")
    failed = await asyncio.to_thread(
        db_rows, database_url, "SELECT status, error_message FROM documents"
    )
    await service.change_status(document, DocumentStatus.PENDING)
    requeued = await asyncio.to_thread(
        db_rows, database_url, "SELECT status, error_message FROM documents"
    )

    assert tuple(failed[0]) == ("failed", "unreadable")
    assert tuple(requeued[0]) == ("pending", None)


# --- Phase 4: processing columns and document_sections -------------------------------------

INSERT_SECTION = (
    "INSERT INTO document_sections (document_id, ordinal, kind, page_number, text, char_count) "
    "VALUES (:d, :o, :k, :p, :t, :c)"
)


def _section(url: str, doc: str, *, ordinal: int = 0, kind: str = "page", page: int | None = 1,
             text: str = "abc", count: int | None = None) -> None:  # fmt: skip
    db_execute(
        url, INSERT_SECTION, d=doc, o=ordinal, k=kind, p=page, t=text,
        c=len(text) if count is None else count,
    )  # fmt: skip


def test_migration_0004_downgrade_and_upgrade_cycle(database_url: str) -> None:
    downgrade(database_url, "0003")
    tables = asyncio.run(_inspect(database_url, lambda c: set(inspect(c).get_table_names())))
    columns = asyncio.run(
        _inspect(
            database_url, lambda c: {col["name"] for col in inspect(c).get_columns("documents")}
        )
    )
    assert "document_sections" not in tables and "documents" in tables
    assert not {"page_count", "failure_reason", "processing_metadata"} & columns

    upgrade(database_url)

    columns = asyncio.run(
        _inspect(
            database_url, lambda c: {col["name"] for col in inspect(c).get_columns("documents")}
        )
    )
    assert {"page_count", "character_count", "failure_reason", "processing_attempts"} <= columns


def test_existing_documents_survive_the_migration_with_defaults(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    doc = _insert_document(database_url, user)
    downgrade(database_url, "0003")
    upgrade(database_url)

    row = db_rows(
        database_url,
        "SELECT status, processing_attempts, processing_metadata::text, page_count "
        "FROM documents WHERE id = :d",
        d=doc,
    )[0]

    assert tuple(row) == ("pending", 0, "{}", None)


def test_sections_store_text_with_matching_character_counts(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    doc = _insert_document(database_url, user)

    _section(database_url, doc, text="বাংলা 😀")  # non-BMP and Bengali count as characters

    assert (
        db_scalar(database_url, "SELECT char_count FROM document_sections") == len("বাংলা 😀") == 7
    )


@pytest.mark.parametrize(
    ("kwargs", "constraint"),
    [
        ({"count": 99}, "char_count_matches"),
        ({"kind": "chapter"}, "kind_valid"),
        ({"kind": "section", "page": 3}, "page_number_iff_page"),
        ({"kind": "page", "page": None}, "page_number_iff_page"),
        ({"page": 0}, "page_number_positive"),
        ({"ordinal": -1}, "ordinal_non_negative"),
    ],
)
def test_section_constraints_reject_bad_rows(
    database_url: str, kwargs: dict[str, object], constraint: str
) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    doc = _insert_document(database_url, user)

    with pytest.raises(IntegrityError, match=constraint):
        _section(database_url, doc, **kwargs)  # type: ignore[arg-type]


def test_section_ordinals_are_unique_per_document_and_cascade_on_delete(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    doc = _insert_document(database_url, user)
    _section(database_url, doc, ordinal=0)

    with pytest.raises(IntegrityError, match="uq_document_sections_document_ordinal"):
        _section(database_url, doc, ordinal=0, page=2)
    db_execute(database_url, "DELETE FROM documents WHERE id = :d", d=doc)

    assert db_scalar(database_url, "SELECT count(*) FROM document_sections") == 0


def test_failure_reason_is_only_allowed_on_failed_documents(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    doc = _insert_document(database_url, user)

    with pytest.raises(IntegrityError, match="failure_only_when_failed"):
        db_execute(database_url, "UPDATE documents SET failure_reason = 'x' WHERE id = :d", d=doc)
    db_execute(
        database_url,
        "UPDATE documents SET status='failed', failure_reason='corrupt_document' WHERE id = :d",
        d=doc,
    )


def test_negative_counts_are_rejected(database_url: str) -> None:
    user = create_user_in_db(database_url, email="u@example.com")
    doc = _insert_document(database_url, user)

    for column in ("page_count", "character_count", "processing_attempts"):
        with pytest.raises(IntegrityError):
            statement = f"UPDATE documents SET {column} = -1 WHERE id = :d"  # noqa: S608
            db_execute(database_url, statement, d=doc)
