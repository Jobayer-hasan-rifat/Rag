import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import make_url

from tests.helpers import (
    create_user_in_db,
    db_autocommit,
    db_execute,
    db_rows,
    db_scalar,
    downgrade,
    upgrade,
)

pytestmark = pytest.mark.integration

LEGACY_DOC = (
    "INSERT INTO documents (id, user_id, filename, storage_key, file_type, content_type, "
    "file_size, checksum_sha256, status) "
    "VALUES (:id, :u, 'f.txt', :k, 'txt', 'text/plain', 10, :c, :s)"
)
INSERT_FAMILY = (
    "INSERT INTO document_families (id, user_id, next_version_number) VALUES (:f, :u, 9)"
)
INSERT_VERSION = (
    "INSERT INTO documents (id, user_id, family_id, version_number, is_active, is_current, "
    "filename, storage_key, file_type, content_type, file_size, checksum_sha256, status) "
    "VALUES (:id, :u, :f, :n, :a, :c, 'f.txt', :k, 'txt', 'text/plain', 10, :h, :s)"
)


@pytest.fixture
def database_url(postgres_url: str) -> Iterator[str]:
    name = f"vers_{uuid.uuid4().hex[:10]}"
    db_autocommit(postgres_url, f'CREATE DATABASE "{name}"')
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    upgrade(url)
    yield url
    db_autocommit(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def legacy_url(postgres_url: str) -> Iterator[str]:
    name = f"legacy_{uuid.uuid4().hex[:10]}"
    db_autocommit(postgres_url, f'CREATE DATABASE "{name}"')
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    upgrade(url, "0008")
    yield url
    db_autocommit(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _family(url: str, user: str) -> str:
    family = str(uuid.uuid4())
    db_execute(url, INSERT_FAMILY, f=family, u=user)
    return family


def _version(
    url: str,
    user: str,
    family: str,
    number: int,
    *,
    status: str = "ready",
    active: bool = False,
    current: bool = False,
) -> str:
    version = str(uuid.uuid4())
    db_execute(
        url,
        INSERT_VERSION,
        id=version,
        u=user,
        f=family,
        n=number,
        a=active,
        c=current,
        k=f"documents/aa/{uuid.uuid4().hex}",
        h=uuid.uuid4().hex + uuid.uuid4().hex,
        s=status,
    )
    return version


# --- the invariants the database enforces -------------------------------------------------------


def test_a_family_has_at_most_one_active_and_one_current_version(database_url: str) -> None:
    user = create_user_in_db(database_url, email="a@example.com")
    family = _family(database_url, user)
    _version(database_url, user, family, 1, active=True, current=True)

    with pytest.raises(Exception, match="uq_documents_one_active"):
        _version(database_url, user, family, 2, active=True, current=True)
    with pytest.raises(Exception, match="uq_documents_one_current"):
        _version(database_url, user, family, 3, current=True)


def test_only_a_ready_version_can_be_made_active(database_url: str) -> None:
    user = create_user_in_db(database_url, email="a@example.com")
    family = _family(database_url, user)

    with pytest.raises(Exception, match="only a ready document version"):
        _version(database_url, user, family, 1, status="pending", active=True, current=True)
    pending = _version(database_url, user, family, 1, status="pending", current=True)
    with pytest.raises(Exception, match="only a ready document version"):
        db_execute(database_url, "UPDATE documents SET is_active = true WHERE id = :i", i=pending)

    db_execute(database_url, "UPDATE documents SET status = 'ready' WHERE id = :i", i=pending)
    db_execute(database_url, "UPDATE documents SET is_active = true WHERE id = :i", i=pending)
    # an active version may leave `ready` for reprocessing: it simply stops being searchable
    db_execute(database_url, "UPDATE documents SET status = 'embedding' WHERE id = :i", i=pending)
    assert (
        db_scalar(database_url, "SELECT is_active FROM documents WHERE id = :i", i=pending) is True
    )


def test_an_active_version_must_be_current(database_url: str) -> None:
    user = create_user_in_db(database_url, email="a@example.com")
    family = _family(database_url, user)

    with pytest.raises(Exception, match="active_is_current"):
        _version(database_url, user, family, 1, active=True, current=False)


def test_version_numbers_are_unique_within_a_family_but_not_across(database_url: str) -> None:
    user = create_user_in_db(database_url, email="a@example.com")
    one, two = _family(database_url, user), _family(database_url, user)
    _version(database_url, user, one, 1)

    with pytest.raises(Exception, match="uq_documents_family_version"):
        _version(database_url, user, one, 1)
    _version(database_url, user, two, 1)  # the same number in another family is fine


def test_a_version_cannot_belong_to_another_users_family(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    bob = create_user_in_db(database_url, email="b@example.com")
    family = _family(database_url, alice)

    with pytest.raises(Exception, match="fk_documents_family_owner"):
        _version(database_url, bob, family, 1)


def test_deleting_a_family_removes_versions_chunks_and_memberships(database_url: str) -> None:
    user = create_user_in_db(database_url, email="a@example.com")
    family = _family(database_url, user)
    version = _version(database_url, user, family, 1, active=True, current=True)
    collection = str(uuid.uuid4())
    db_execute(
        database_url,
        "INSERT INTO collections (id, user_id, name) VALUES (:c, :u, 'c')",
        c=collection,
        u=user,
    )
    db_execute(
        database_url,
        "INSERT INTO document_collections (document_id, collection_id, user_id) "
        "VALUES (:f, :c, :u)",
        f=family,
        c=collection,
        u=user,
    )
    db_execute(
        database_url,
        "INSERT INTO document_sections (id, document_id, ordinal, kind, text, char_count) "
        "VALUES (:s, :d, 0, 'body', 'abc', 3)",
        s=str(uuid.uuid4()),
        d=version,
    )

    db_execute(database_url, "DELETE FROM document_families WHERE id = :f", f=family)

    for table in ("documents", "document_sections", "document_collections"):
        assert db_scalar(database_url, f"SELECT count(*) FROM {table}") == 0


def test_membership_must_name_a_document_of_the_same_owner(database_url: str) -> None:
    alice = create_user_in_db(database_url, email="a@example.com")
    bob = create_user_in_db(database_url, email="b@example.com")
    family = _family(database_url, alice)
    collection = str(uuid.uuid4())
    db_execute(
        database_url,
        "INSERT INTO collections (id, user_id, name) VALUES (:c, :u, 'c')",
        c=collection,
        u=bob,
    )

    with pytest.raises(Exception, match="fk_document_collections_document"):
        db_execute(
            database_url,
            "INSERT INTO document_collections (document_id, collection_id, user_id) "
            "VALUES (:f, :c, :u)",
            f=family,
            c=collection,
            u=bob,
        )


# --- migration 0009 -------------------------------------------------------------------------------


def _legacy(url: str, user: str, status: str) -> str:
    doc = str(uuid.uuid4())
    db_execute(
        url,
        LEGACY_DOC,
        id=doc,
        u=user,
        k=f"documents/aa/{uuid.uuid4().hex}",
        c=uuid.uuid4().hex + uuid.uuid4().hex,
        s=status,
    )
    return doc


def test_the_backfill_gives_every_existing_document_one_valid_first_version(
    legacy_url: str,
) -> None:
    user = create_user_in_db(legacy_url, email="a@example.com")
    ready, pending, failed = (_legacy(legacy_url, user, s) for s in ("ready", "pending", "failed"))
    collection = str(uuid.uuid4())
    db_execute(
        legacy_url,
        "INSERT INTO collections (id, user_id, name) VALUES (:c, :u, 'c')",
        c=collection,
        u=user,
    )
    db_execute(
        legacy_url,
        "INSERT INTO document_collections (document_id, collection_id, user_id) "
        "VALUES (:d, :c, :u)",
        d=ready,
        c=collection,
        u=user,
    )

    upgrade(legacy_url)

    rows = {
        str(r[0]): r[1:]
        for r in db_rows(
            legacy_url,
            "SELECT id, family_id::text, version_number, is_active, is_current FROM documents",
        )
    }
    assert rows[ready] == (ready, 1, True, True)  # the family takes the document's id
    assert rows[pending] == (pending, 1, False, True)
    assert rows[failed] == (failed, 1, False, True)
    assert db_scalar(legacy_url, "SELECT count(*) FROM document_families") == 3
    assert db_scalar(legacy_url, "SELECT next_version_number FROM document_families LIMIT 1") == 2
    assert db_scalar(legacy_url, "SELECT count(*) FROM document_collections") == 1


def test_downgrade_restores_the_old_shape_when_every_document_has_one_version(
    legacy_url: str,
) -> None:
    user = create_user_in_db(legacy_url, email="a@example.com")
    doc = _legacy(legacy_url, user, "ready")
    upgrade(legacy_url)

    downgrade(legacy_url, "0008")

    columns = {
        r[0]
        for r in db_rows(
            legacy_url,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'",
        )
    }
    assert not columns & {"family_id", "version_number", "is_active", "is_current"}
    assert db_scalar(legacy_url, "SELECT count(*) FROM documents WHERE id = :i", i=doc) == 1
    upgrade(legacy_url)  # and it can be applied again
    assert db_scalar(legacy_url, "SELECT count(*) FROM document_families") == 1


def test_downgrade_refuses_to_lose_versions_and_works_after_they_are_deleted(
    legacy_url: str,
) -> None:
    user = create_user_in_db(legacy_url, email="a@example.com")
    first = _legacy(legacy_url, user, "ready")
    collection = str(uuid.uuid4())
    db_execute(
        legacy_url,
        "INSERT INTO collections (id, user_id, name) VALUES (:c, :u, 'c')",
        c=collection,
        u=user,
    )
    db_execute(
        legacy_url,
        "INSERT INTO document_collections (document_id, collection_id, user_id) "
        "VALUES (:d, :c, :u)",
        d=first,
        c=collection,
        u=user,
    )
    upgrade(legacy_url)
    second = _version(legacy_url, user, first, 2)
    db_execute(
        legacy_url,
        "UPDATE documents SET is_active = false, is_current = false WHERE id = :i",
        i=first,
    )
    db_execute(
        legacy_url,
        "UPDATE documents SET is_active = true, is_current = true WHERE id = :i",
        i=second,
    )

    with pytest.raises(Exception, match="more than one version"):
        downgrade(legacy_url, "0008")
    assert db_scalar(legacy_url, "SELECT count(*) FROM documents") == 2  # nothing was changed

    db_execute(legacy_url, "DELETE FROM documents WHERE id = :i", i=first)
    downgrade(legacy_url, "0008")

    # membership now names the surviving version row
    assert db_scalar(legacy_url, "SELECT document_id::text FROM document_collections") == second
