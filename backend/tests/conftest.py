import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
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
