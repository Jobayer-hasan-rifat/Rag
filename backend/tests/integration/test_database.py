from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings
from app.db.health import check_database
from app.db.session import create_engine, create_session_factory

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(infra_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(infra_settings)
    yield engine
    await engine.dispose()


async def test_engine_connects_and_runs_a_query(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


async def test_session_factory_provides_working_sessions(engine: AsyncEngine) -> None:
    factory = create_session_factory(engine)

    async with factory() as session:
        result = await session.execute(text("SELECT current_database()"))

    assert result.scalar_one() == "test"


async def test_check_database_succeeds_against_live_database(engine: AsyncEngine) -> None:
    await check_database(engine)


async def test_check_database_raises_when_database_is_unreachable() -> None:
    dead = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    try:
        with pytest.raises(OSError):
            await check_database(dead)
    finally:
        await dead.dispose()


async def test_pgvector_extension_can_be_created_and_used(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        distance = await connection.execute(text("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector"))

    assert distance.scalar_one() == pytest.approx(1.0)
