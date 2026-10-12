import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]


async def _execute_autocommit(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


async def _scalar(url: str, statement: str) -> object:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return (await connection.execute(text(statement))).scalar_one_or_none()
    finally:
        await engine.dispose()


@pytest.fixture
def fresh_database_url(postgres_url: str) -> Iterator[str]:
    name = f"migration_{uuid.uuid4().hex[:12]}"
    asyncio.run(_execute_autocommit(postgres_url, f'CREATE DATABASE "{name}"'))
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    yield url
    asyncio.run(_execute_autocommit(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def alembic_config(fresh_database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", fresh_database_url.replace("%", "%%"))
    return config


def _has_vector(url: str) -> bool:
    return (
        asyncio.run(_scalar(url, "SELECT extname FROM pg_extension WHERE extname = 'vector'"))
        == "vector"
    )


def test_upgrade_head_enables_pgvector(alembic_config: Config, fresh_database_url: str) -> None:
    assert not _has_vector(fresh_database_url)

    command.upgrade(alembic_config, "head")

    assert _has_vector(fresh_database_url)
    version = asyncio.run(_scalar(fresh_database_url, "SELECT version_num FROM alembic_version"))
    assert version == "0007"


def test_downgrade_to_base_removes_pgvector(
    alembic_config: Config, fresh_database_url: str
) -> None:
    command.upgrade(alembic_config, "head")

    command.downgrade(alembic_config, "base")

    assert not _has_vector(fresh_database_url)


def test_downgrade_one_step_keeps_pgvector(alembic_config: Config, fresh_database_url: str) -> None:
    command.upgrade(alembic_config, "head")

    command.downgrade(alembic_config, "-1")

    assert _has_vector(fresh_database_url)


def test_upgrade_is_repeatable_after_downgrade(
    alembic_config: Config, fresh_database_url: str
) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "-1")

    command.upgrade(alembic_config, "head")

    assert _has_vector(fresh_database_url)


def test_upgrade_head_is_idempotent(alembic_config: Config, fresh_database_url: str) -> None:
    command.upgrade(alembic_config, "head")
    command.upgrade(alembic_config, "head")

    assert _has_vector(fresh_database_url)


def test_migration_history_has_a_single_head(alembic_config: Config) -> None:
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(alembic_config).get_heads()

    assert heads == ["0007"]


async def _seed_legacy_chunk(url: str) -> None:
    """One chunk written before 0007 existed (no owner column, no trigger)."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL session_replication_role = replica"))
            await connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, password_hash, role_id) VALUES "
                    "('00000000-0000-0000-0000-0000000000a1', 'a@example.com', 'A', 'x', 1)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO documents (id, user_id, filename, storage_key, file_type, "
                    "content_type, file_size, checksum_sha256, status) VALUES "
                    "('00000000-0000-0000-0000-0000000000d1', "
                    "'00000000-0000-0000-0000-0000000000a1', 'a.txt', 'k', 'txt', 'text/plain', "
                    "5, repeat('0', 64), 'ready')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO document_chunks (document_id, section_id, chunking_version, "
                    "chunk_index, text, char_count, text_sha256, start_char, end_char) VALUES "
                    "('00000000-0000-0000-0000-0000000000d1', "
                    "'00000000-0000-0000-0000-0000000000e1', 'v1', 0, 'hello', 5, "
                    "repeat('0', 64), 0, 5)"
                )
            )
    finally:
        await engine.dispose()


def test_upgrade_backfills_existing_chunks_and_downgrade_restores_the_old_shape(
    alembic_config: Config, fresh_database_url: str
) -> None:
    command.upgrade(alembic_config, "0006")
    asyncio.run(_seed_legacy_chunk(fresh_database_url))

    command.upgrade(alembic_config, "0007")

    owner = asyncio.run(_scalar(fresh_database_url, "SELECT user_id::text FROM document_chunks"))
    assert owner == "00000000-0000-0000-0000-0000000000a1"
    matched = asyncio.run(
        _scalar(
            fresh_database_url,
            "SELECT count(*) FROM document_chunks "
            "WHERE search_vector @@ plainto_tsquery('simple', 'hello')",
        )
    )
    assert matched == 1

    command.downgrade(alembic_config, "0006")

    columns = asyncio.run(
        _scalar(
            fresh_database_url,
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'document_chunks' "
            "AND column_name IN ('user_id', 'search_vector')",
        )
    )
    assert columns == 0
    command.upgrade(alembic_config, "head")  # and it comes back cleanly
