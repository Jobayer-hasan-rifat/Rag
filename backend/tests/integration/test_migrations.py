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
    assert version == "0002"


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

    assert heads == ["0002"]
