import uuid
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.embeddings.constants import VECTOR_DIMENSION
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
    "file_size, checksum_sha256, chunk_count) "
    "VALUES (:id, :u, 'f.txt', :k, 'txt', 'text/plain', 10, :c, 1)"
)
INSERT_SECTION = (
    "INSERT INTO document_sections (id, document_id, ordinal, kind, text, char_count) "
    "VALUES (:s, :d, 0, 'body', 'abcde', 5)"
)
INSERT_CHUNK = (
    "INSERT INTO document_chunks (id, document_id, section_id, chunking_version, chunk_index, "
    "text, char_count, text_sha256, start_char, end_char) "
    "VALUES (:i, :d, :s, 'c1.0', 0, 'abcde', 5, :h, 0, 5)"
)
INSERT_CONFIG = (
    "INSERT INTO embedding_configs (id, fingerprint, provider, model, revision, dimension, "
    "metric, normalized, max_seq_length, include_heading, preprocessing_version) "
    "VALUES (:i, :f, 'p', 'm', 'r', 384, 'cosine', true, 512, false, 'e1')"
)
INSERT_VECTOR = (
    "INSERT INTO chunk_embeddings "
    "(chunk_id, document_id, user_id, config_id, embedding, text_sha256) "
    "VALUES (:ch, :d, :u, :cfg, CAST(:v AS vector), :h)"
)


def vector(dim: int = VECTOR_DIMENSION, first: float = 1.0) -> str:
    return "[" + ",".join([str(first)] + ["0"] * (dim - 1)) + "]"


class World:
    """One user with a document, section, chunk and embedding configuration."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.user = create_user_in_db(url, email=f"{uuid.uuid4().hex[:8]}@example.com")
        self.doc, self.section, self.chunk, self.config = (str(uuid.uuid4()) for _ in range(4))
        self.sha = "a" * 64
        db_execute(
            url, INSERT_DOC, id=self.doc, u=self.user, k=f"documents/aa/{uuid.uuid4().hex}",
            c=uuid.uuid4().hex * 2,
        )  # fmt: skip
        db_execute(url, INSERT_SECTION, s=self.section, d=self.doc)
        db_execute(url, INSERT_CHUNK, i=self.chunk, d=self.doc, s=self.section, h=self.sha)
        db_execute(url, INSERT_CONFIG, i=self.config, f=uuid.uuid4().hex * 2)

    def embed(self, **overrides: Any) -> None:
        values: dict[str, Any] = {
            "ch": self.chunk, "d": self.doc, "u": self.user, "cfg": self.config,
            "v": vector(), "h": self.sha,
        }  # fmt: skip
        values.update(overrides)
        db_execute(self.url, INSERT_VECTOR, **values)


@pytest.fixture
def world(migrated_database_url: str) -> World:
    return World(migrated_database_url)


def test_a_vector_is_stored_with_its_dimension(world: World) -> None:
    world.embed()

    dims = db_scalar(
        world.url,
        "SELECT vector_dims(embedding) FROM chunk_embeddings WHERE chunk_id = :c",
        c=world.chunk,
    )
    assert dims == VECTOR_DIMENSION


@pytest.mark.parametrize("dim", [3, VECTOR_DIMENSION - 1, VECTOR_DIMENSION + 1, 768])
def test_a_vector_of_the_wrong_dimension_is_rejected_by_the_database(
    world: World, dim: int
) -> None:
    with pytest.raises(DBAPIError, match="dimensions"):
        world.embed(v=vector(dim))


def test_a_chunk_has_at_most_one_vector_per_configuration(world: World) -> None:
    world.embed()

    with pytest.raises(IntegrityError, match="uq_chunk_embeddings_chunk_config"):
        world.embed()


def test_a_chunk_can_have_vectors_under_different_configurations(world: World) -> None:
    other = str(uuid.uuid4())
    db_execute(world.url, INSERT_CONFIG, i=other, f=uuid.uuid4().hex * 2)
    world.embed()

    world.embed(cfg=other)

    count = db_scalar(
        world.url, "SELECT count(*) FROM chunk_embeddings WHERE chunk_id = :c", c=world.chunk
    )
    assert count == 2


def test_a_vector_cannot_claim_a_chunk_of_another_document(migrated_database_url: str) -> None:
    mine, theirs = World(migrated_database_url), World(migrated_database_url)

    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_chunk"):
        mine.embed(ch=theirs.chunk)  # my document id with their chunk


def test_a_vector_cannot_carry_an_owner_other_than_its_documents(
    migrated_database_url: str,
) -> None:
    mine, theirs = World(migrated_database_url), World(migrated_database_url)

    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_document_owner"):
        mine.embed(u=theirs.user)


def test_a_vector_needs_an_existing_configuration(world: World) -> None:
    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_config"):
        world.embed(cfg=str(uuid.uuid4()))


def test_a_configuration_in_use_cannot_be_deleted(world: World) -> None:
    world.embed()

    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_config"):
        db_execute(world.url, "DELETE FROM embedding_configs WHERE id = :i", i=world.config)


def test_configurations_are_unique_by_fingerprint(world: World) -> None:
    fingerprint = db_scalar(
        world.url, "SELECT fingerprint FROM embedding_configs WHERE id = :i", i=world.config
    )

    with pytest.raises(IntegrityError, match="embedding_configs_fingerprint"):
        db_execute(world.url, INSERT_CONFIG, i=str(uuid.uuid4()), f=fingerprint)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("dimension", 0, "dimension_positive"),
        ("max_seq_length", 0, "max_seq_length_positive"),
        ("metric", "'dot'", "metric_valid"),
    ],
)
def test_configuration_constraints_reject_bad_rows(
    world: World, column: str, value: Any, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        db_execute(
            world.url,
            f"UPDATE embedding_configs SET {column} = {value} WHERE id = :i",
            i=world.config,
        )


def test_the_text_hash_must_be_a_sha256(world: World) -> None:
    with pytest.raises(IntegrityError, match="text_sha256_length"):
        world.embed(h="short")


def test_deleting_a_chunk_deletes_its_vectors(world: World) -> None:
    world.embed()

    db_execute(world.url, "DELETE FROM document_chunks WHERE id = :c", c=world.chunk)

    assert (
        db_scalar(
            world.url, "SELECT count(*) FROM chunk_embeddings WHERE document_id = :d", d=world.doc
        )
        == 0
    )


def test_deleting_a_document_deletes_its_vectors_and_chunks(world: World) -> None:
    world.embed()

    db_execute(world.url, "DELETE FROM documents WHERE id = :d", d=world.doc)

    assert (
        db_scalar(
            world.url, "SELECT count(*) FROM chunk_embeddings WHERE document_id = :d", d=world.doc
        )
        == 0
    )
    assert (
        db_scalar(
            world.url, "SELECT count(*) FROM document_chunks WHERE document_id = :d", d=world.doc
        )
        == 0
    )


def test_deleting_a_user_deletes_their_vectors(world: World) -> None:
    world.embed()

    db_execute(world.url, "DELETE FROM users WHERE id = :u", u=world.user)

    assert (
        db_scalar(
            world.url, "SELECT count(*) FROM chunk_embeddings WHERE user_id = :u", u=world.user
        )
        == 0
    )


def test_cosine_distance_orders_vectors_as_expected(world: World) -> None:
    other_chunk = str(uuid.uuid4())
    db_execute(
        world.url,
        "INSERT INTO document_chunks (id, document_id, section_id, chunking_version, chunk_index, "
        "text, char_count, text_sha256, start_char, end_char) "
        "VALUES (:i, :d, :s, 'c1.0', 1, 'fghij', 5, :h, 0, 5)",
        i=other_chunk, d=world.doc, s=world.section, h="b" * 64,
    )  # fmt: skip
    world.embed(v=vector(first=1.0))
    world.embed(
        ch=other_chunk,
        v="[" + ",".join(["1", "1"] + ["0"] * (VECTOR_DIMENSION - 2)) + "]",
        h="b" * 64,
    )

    rows = db_rows(
        world.url,
        "SELECT chunk_id::text, round((embedding <=> CAST(:q AS vector))::numeric, 4) "
        "FROM chunk_embeddings WHERE document_id = :d ORDER BY embedding <=> CAST(:q AS vector)",
        q=vector(first=1.0),
        d=world.doc,
    )
    assert [r[0] for r in rows] == [world.chunk, other_chunk]
    assert float(rows[0][1]) == 0.0 and abs(float(rows[1][1]) - 0.2929) < 1e-3


def test_document_embedding_columns_have_safe_defaults_and_constraints(world: World) -> None:
    row = db_rows(
        world.url,
        "SELECT embedding_attempts, embedding_started_at, embedded_chunk_count, "
        "embedding_config_id "
        "FROM documents WHERE id = :d",
        d=world.doc,
    )[0]
    assert tuple(row) == (0, None, None, None)
    with pytest.raises(IntegrityError, match="embedding_attempts_non_negative"):
        db_execute(
            world.url, "UPDATE documents SET embedding_attempts = -1 WHERE id = :d", d=world.doc
        )
    with pytest.raises(IntegrityError, match="embedded_chunk_count_non_negative"):
        db_execute(
            world.url, "UPDATE documents SET embedded_chunk_count = -1 WHERE id = :d", d=world.doc
        )


def test_deleting_a_configuration_row_clears_documents_that_point_at_it(
    migrated_database_url: str,
) -> None:
    world = World(migrated_database_url)
    db_execute(
        world.url,
        "UPDATE documents SET embedding_config_id = :c WHERE id = :d",
        c=world.config,
        d=world.doc,
    )

    db_execute(world.url, "DELETE FROM embedding_configs WHERE id = :c", c=world.config)

    assert (
        db_scalar(world.url, "SELECT embedding_config_id FROM documents WHERE id = :d", d=world.doc)
        is None
    )


def test_migration_0006_downgrade_restores_phase_five_and_upgrade_is_repeatable(
    migrated_database_url: str,
) -> None:
    world = World(migrated_database_url)
    world.embed()
    db_execute(world.url, "UPDATE documents SET status = 'ready' WHERE id = :d", d=world.doc)
    try:
        downgrade(world.url, "0005")

        tables = {
            r[0]
            for r in db_rows(
                world.url,
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
            )
        }
        assert not {"chunk_embeddings", "embedding_configs"} & tables
        columns = {
            r[0]
            for r in db_rows(
                world.url,
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'",
            )
        }
        assert not {"embedding_attempts", "embedding_config_id", "embedded_chunk_count"} & columns
        assert (
            db_scalar(world.url, "SELECT status FROM documents WHERE id = :d", d=world.doc)
            == "chunked"
        )
        assert (
            db_scalar(
                world.url,
                "SELECT count(*) FROM document_chunks WHERE document_id = :d",
                d=world.doc,
            )
            == 1
        )
    finally:
        upgrade(world.url)

    assert db_scalar(world.url, "SELECT to_regclass('chunk_embeddings')") is not None
    assert db_scalar(world.url, "SELECT count(*) FROM chunk_embeddings") == 0
