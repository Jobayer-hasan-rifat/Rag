import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.repositories.user_repository import RoleRepository
from app.models.user import User
from app.security.password import PasswordHasher

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PASSWORD = "Str0ngPassw0rd"


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)


async def _execute(url: str, statement: str, params: dict[str, Any], autocommit: bool) -> Any:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT" if autocommit else None)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), params)
            return result.fetchall() if result.returns_rows else None
    finally:
        await engine.dispose()


def db_execute(url: str, statement: str, **params: Any) -> None:
    asyncio.run(_execute(url, statement, params, autocommit=False))


def db_autocommit(url: str, statement: str) -> None:
    asyncio.run(_execute(url, statement, {}, autocommit=True))


def db_rows(url: str, statement: str, **params: Any) -> list[Any]:
    rows: list[Any] = asyncio.run(_execute(url, statement, params, autocommit=False))
    return rows


def db_scalar(url: str, statement: str, **params: Any) -> Any:
    rows = db_rows(url, statement, **params)
    return rows[0][0] if rows else None


async def _create_user(
    url: str,
    *,
    email: str,
    password: str,
    role: str,
    is_active: bool,
    deleted: bool,
    display_name: str,
    cost: int,
) -> str:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await _insert_user(
                session,
                email=email,
                password_hash=PasswordHasher(cost).hash_sync(password),
                role=role,
                is_active=is_active,
                deleted=deleted,
                display_name=display_name,
            )
    finally:
        await engine.dispose()


async def _insert_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str,
    role: str,
    is_active: bool,
    deleted: bool,
    display_name: str,
) -> str:
    found = await RoleRepository(session).get_by_name(role)
    assert found is not None
    user = User(
        email=email,
        display_name=display_name,
        password_hash=password_hash,
        role_id=found.id,
        is_active=is_active,
    )
    if deleted:
        user.deleted_at = datetime.now(UTC)
    session.add(user)
    await session.commit()
    return str(user.id)


def create_user_in_db(
    url: str,
    *,
    email: str = "seed@example.com",
    password: str = DEFAULT_PASSWORD,
    role: str = "user",
    is_active: bool = True,
    deleted: bool = False,
    display_name: str = "Seed User",
    cost: int = 4,
) -> str:
    return asyncio.run(
        _create_user(
            url,
            email=email,
            password=password,
            role=role,
            is_active=is_active,
            deleted=deleted,
            display_name=display_name,
            cost=cost,
        )
    )


def register(
    client: TestClient,
    *,
    email: str = "alice@example.com",
    password: str = DEFAULT_PASSWORD,
    display_name: str = "Alice",
) -> Response:
    response: Response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )
    return response


def login(
    client: TestClient, *, email: str = "alice@example.com", password: str = DEFAULT_PASSWORD
) -> Response:
    response: Response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return response


def tokens(response: Response) -> dict[str, Any]:
    data: dict[str, Any] = response.json()["data"]
    return data


def register_and_login(
    client: TestClient, *, email: str = "alice@example.com", password: str = DEFAULT_PASSWORD
) -> dict[str, Any]:
    assert register(client, email=email, password=password).status_code == 201
    response = login(client, email=email, password=password)
    assert response.status_code == 200
    return tokens(response)


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


class RecordingQueue:
    """Stands in for the broker where a test builds DocumentService directly."""

    def __init__(self) -> None:
        self.enqueued: list[Any] = []

    async def enqueue(self, document_id: Any) -> bool:
        self.enqueued.append(document_id)
        return True
