import asyncio
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from app.db.base import Base
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


@pytest.fixture
def database_url(postgres_url: str) -> Iterator[str]:
    import uuid

    name = f"schema_{uuid.uuid4().hex[:10]}"
    db_autocommit(postgres_url, f'CREATE DATABASE "{name}"')
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    upgrade(url)
    yield url
    db_autocommit(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def _inspect(url: str, fn):  # type: ignore[no-untyped-def]
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(fn)
    finally:
        await engine.dispose()


def _tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def test_roles_are_seeded(database_url: str) -> None:
    rows = db_rows(database_url, "SELECT name FROM roles ORDER BY name")

    assert [row[0] for row in rows] == ["admin", "user"]


def test_upgrade_downgrade_upgrade_cycle(database_url: str) -> None:
    downgrade(database_url, "0001")
    assert not {"users", "roles", "refresh_tokens"} & asyncio.run(_inspect(database_url, _tables))

    upgrade(database_url)

    assert {"users", "roles", "refresh_tokens"} <= asyncio.run(_inspect(database_url, _tables))
    assert db_scalar(database_url, "SELECT count(*) FROM roles") == 2


def test_downgrade_to_base_removes_everything_including_pgvector(database_url: str) -> None:
    downgrade(database_url, "base")

    assert db_scalar(database_url, "SELECT count(*) FROM pg_extension WHERE extname='vector'") == 0


def _indexes(connection: Connection) -> dict[str, set[str]]:
    insp = inspect(connection)
    return {
        table: {str(index["name"]) for index in insp.get_indexes(table)}
        | {str(c["name"]) for c in insp.get_unique_constraints(table)}
        for table in ("users", "roles", "refresh_tokens")
    }


def test_expected_indexes_and_unique_constraints_exist(database_url: str) -> None:
    found = asyncio.run(_inspect(database_url, _indexes))

    assert {"uq_users_email", "ix_users_role_id"} <= found["users"]
    assert "uq_roles_name" in found["roles"]
    assert {
        "uq_refresh_tokens_token_hash",
        "ix_refresh_tokens_user_id",
        "ix_refresh_tokens_family_id",
        "ix_refresh_tokens_expires_at",
    } <= found["refresh_tokens"]


def test_migrated_schema_matches_the_orm_models(database_url: str) -> None:
    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection)
        return list(compare_metadata(context, Base.metadata))

    assert asyncio.run(_inspect(database_url, diff)) == []


def test_email_must_be_unique(database_url: str) -> None:
    create_user_in_db(database_url, email="dup@example.com")

    with pytest.raises(IntegrityError):
        create_user_in_db(database_url, email="dup@example.com")


def test_uppercase_email_is_rejected_by_database_constraint(database_url: str) -> None:
    with pytest.raises(IntegrityError, match="email_lowercase"):
        create_user_in_db(database_url, email="Mixed@Example.com")


def test_empty_display_name_is_rejected_by_database_constraint(database_url: str) -> None:
    with pytest.raises(IntegrityError, match="display_name_length"):
        create_user_in_db(database_url, email="a@example.com", display_name="")


def test_deleting_a_user_cascades_to_refresh_tokens(database_url: str) -> None:
    user_id = create_user_in_db(database_url, email="cascade@example.com")
    db_execute(
        database_url,
        "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at) "
        "VALUES (:u, gen_random_uuid(), :h, now() + interval '1 day')",
        u=user_id,
        h="a" * 64,
    )

    db_execute(database_url, "DELETE FROM users WHERE id = :u", u=user_id)

    assert db_scalar(database_url, "SELECT count(*) FROM refresh_tokens") == 0


def test_a_role_in_use_cannot_be_deleted(database_url: str) -> None:
    create_user_in_db(database_url, email="role@example.com")

    with pytest.raises(IntegrityError):
        db_execute(database_url, "DELETE FROM roles WHERE name = 'user'")


def test_refresh_token_must_expire_after_creation(database_url: str) -> None:
    user_id = create_user_in_db(database_url, email="ttl@example.com")

    with pytest.raises(IntegrityError, match="expires_after_creation"):
        db_execute(
            database_url,
            "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at) "
            "VALUES (:u, gen_random_uuid(), :h, now() - interval '1 day')",
            u=user_id,
            h="b" * 64,
        )


def test_refresh_token_hash_must_be_unique(database_url: str) -> None:
    user_id = create_user_in_db(database_url, email="uniq@example.com")
    insert = (
        "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at) "
        "VALUES (:u, gen_random_uuid(), :h, now() + interval '1 day')"
    )
    db_execute(database_url, insert, u=user_id, h="c" * 64)

    with pytest.raises(IntegrityError):
        db_execute(database_url, insert, u=user_id, h="c" * 64)


def test_timestamps_and_defaults_are_populated(database_url: str) -> None:
    create_user_in_db(database_url, email="ts@example.com")

    row = db_rows(
        database_url,
        "SELECT created_at IS NOT NULL, updated_at IS NOT NULL, is_active, deleted_at IS NULL "
        "FROM users WHERE email='ts@example.com'",
    )[0]

    assert tuple(row) == (True, True, True, True)
