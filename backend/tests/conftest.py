import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any

import pytest
import redis as redis_sync
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importing app.workers.celery_app builds a Celery app from settings at import time.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:1/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://127.0.0.1:1/2")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-0123456789abcdef")

from app.config import Settings
from app.main import create_app

TEST_JWT_SECRET = "test-only-secret-key-0123456789abcdef"
POSTGRES_IMAGE = "pgvector/pgvector:pg16"
REDIS_IMAGE = "redis:7-alpine"
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://nobody:secret-db-password@127.0.0.1:1/none"
UNREACHABLE_REDIS_URL = "redis://:secret-redis-password@127.0.0.1:1/0"

SettingsFactory = Callable[..., Settings]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        yield external
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(POSTGRES_IMAGE, username="test", password="test", dbname="test") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql+asyncpg://test:test@{host}:{port}/test"


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    external = os.environ.get("TEST_REDIS_URL")
    if external:
        yield external
        return

    from testcontainers.redis import RedisContainer

    with RedisContainer(REDIS_IMAGE) as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}"


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "app_env": "test",
            "database_url": UNREACHABLE_DATABASE_URL,
            "redis_url": UNREACHABLE_REDIS_URL,
            "celery_broker_url": UNREACHABLE_REDIS_URL,
            "celery_result_backend": UNREACHABLE_REDIS_URL,
            "jwt_secret_key": TEST_JWT_SECRET,
            "cors_origins": ["http://localhost:5173"],
            "log_level": "WARNING",
            "bcrypt_cost_factor": 4,
            "rate_limit_auth_attempts": 1000,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@pytest.fixture
def infra_settings(make_settings: SettingsFactory, postgres_url: str, redis_url: str) -> Settings:
    return make_settings(
        database_url=postgres_url,
        redis_url=f"{redis_url}/0",
        celery_broker_url=f"{redis_url}/1",
        celery_result_backend=f"{redis_url}/2",
    )


@pytest.fixture
def offline_client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    """App whose dependencies are unreachable; the app itself still starts."""
    with TestClient(create_app(make_settings()), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def infra_client(infra_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(infra_settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="session")
def migrated_database_url(postgres_url: str) -> Iterator[str]:
    """A dedicated database migrated to head with the real Alembic migrations."""
    from sqlalchemy.engine import make_url

    from tests.helpers import db_autocommit, upgrade

    name = f"app_{uuid.uuid4().hex[:12]}"
    db_autocommit(postgres_url, f'CREATE DATABASE "{name}"')
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    upgrade(url)
    yield url
    db_autocommit(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def clean_auth_state(migrated_database_url: str, redis_url: str) -> None:
    from tests.helpers import db_execute

    db_execute(migrated_database_url, "TRUNCATE refresh_tokens, users RESTART IDENTITY CASCADE")
    client = redis_sync.Redis.from_url(f"{redis_url}/0")
    try:
        client.flushdb()
    finally:
        client.close()


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def auth_client_factory(
    make_settings: SettingsFactory,
    migrated_database_url: str,
    redis_url: str,
    clean_auth_state: None,
) -> Iterator[ClientFactory]:
    stack = ExitStack()

    def factory(configure: Callable[[FastAPI], None] | None = None, **overrides: Any) -> TestClient:
        settings = make_settings(
            database_url=migrated_database_url,
            redis_url=f"{redis_url}/0",
            celery_broker_url=f"{redis_url}/1",
            celery_result_backend=f"{redis_url}/2",
            **overrides,
        )
        app = create_app(settings)
        if configure:
            configure(app)
        return stack.enter_context(TestClient(app, raise_server_exceptions=False))

    yield factory
    stack.close()


@pytest.fixture
def auth_client(auth_client_factory: ClientFactory) -> TestClient:
    return auth_client_factory()
