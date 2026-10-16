"""The system under evaluation: the real application (HTTP API, database, pipeline) in process.

Two profiles share one code path:

- `deterministic`: hashing embeddings, a fake reranker and a scripted generation model. No model
  is downloaded and no ML package is imported, so it runs in the lightweight CI environment (it
  still needs Docker for PostgreSQL and Redis, like the integration tests). Quality numbers from
  this profile describe the *harness and the application's gates*, not model quality.
- `models`: the configured real embedding model and reranker (from the local model cache; nothing
  is downloaded unless `--allow-download`) and, if a model name is given, a real generation model
  through the configured provider. Opt-in.

Every request to the generation model is recorded (the prompt is kept in memory for checks and
never written to a report).
"""

import contextlib
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

# Importing the application builds a Celery app from the environment at import time.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://eval:eval@127.0.0.1:1/eval")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:1/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://127.0.0.1:1/2")
os.environ.setdefault("JWT_SECRET_KEY", "evaluation-only-secret-key-0123456789")

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.config import Settings
from app.llm.registry import _FACTORIES, get_llm_provider, register_llm_provider
from app.llm.types import GenerationRequest, GenerationResult
from app.main import create_app
from tests.conftest import POSTGRES_IMAGE, REDIS_IMAGE
from tests.helpers import DEFAULT_PASSWORD, create_user_in_db, db_autocommit, upgrade

PROFILES = ("deterministic", "models")


class RecordingLLM:
    """Wraps a real provider and keeps every request, so prompts can be checked afterwards."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.config = inner.config
        self.requests: list[GenerationRequest] = []
        self.script = None  # scripts only apply to the deterministic profile

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        result: GenerationResult = await self.inner.generate(request)
        return result


@dataclass
class Stack:
    client: TestClient
    database_url: str
    settings: Settings
    profile: str
    llm: Any
    users: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def attach(
        cls, client: TestClient, database_url: str, profile: str = "deterministic"
    ) -> "Stack":
        """Wrap an application a test already built (its fixtures own the lifecycle)."""
        settings = client.app.state.settings  # type: ignore[attr-defined]
        return cls(client, database_url, settings, profile, get_llm_provider(settings))


def settings_values(database_url: str, redis_url: str, storage: str) -> dict[str, Any]:
    """Settings of the deterministic profile (no model, no network)."""
    return {
        "app_env": "test",
        "database_url": database_url,
        "redis_url": f"{redis_url}/0",
        "celery_broker_url": f"{redis_url}/1",
        "celery_result_backend": f"{redis_url}/2",
        "jwt_secret_key": "evaluation-only-secret-key-0123456789",
        "cors_origins": ["http://localhost:5173"],
        "log_level": "WARNING",
        "bcrypt_cost_factor": 4,
        "rate_limit_enabled": False,
        "max_upload_bytes": 2 * 1024 * 1024,
        "storage_local_path": storage,
        "embedding_provider": "test_hash",
        "embedding_model": "test-hash",
        "embedding_model_revision": "test-revision-1",
        "embedding_batch_size": 4,
        "embedding_query_prefix": "",
        "embedding_passage_prefix": "",
        "rerank_enabled": True,
        "rerank_provider": "test_fake",
        "rerank_model": "test-fake",
        "rerank_model_revision": "test-revision-1",
        "rerank_preload": False,
        "rag_enabled": True,
        "llm_provider": "test_llm",
        "llm_model": "test-model",
        "llm_base_url": "http://localhost:11434",
        "search_max_top_k": 50,
    }


def _register_deterministic() -> None:
    from app.embeddings.registry import register_provider
    from app.reranking.registry import register_reranker
    from tests.embedding_fakes import HashProvider
    from tests.llm_fakes import FakeLLM
    from tests.rerank_fakes import FakeReranker

    register_provider("test_hash", HashProvider)
    register_reranker("test_fake", FakeReranker)
    register_llm_provider("test_llm", FakeLLM)


@contextlib.contextmanager
def open_stack(
    profile: str,
    *,
    llm_model: str | None = None,
    llm_provider: str = "ollama",
    llm_base_url: str = "http://127.0.0.1:11434",
    allow_download: bool = False,
) -> Iterator[Stack]:
    """Start PostgreSQL and Redis, migrate, build the application.

    `TEST_DATABASE_URL` and `TEST_REDIS_URL` (as in CI, where they are service containers) are used
    when set; otherwise throwaway containers are started with Testcontainers."""
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}")
    if profile == "deterministic":
        _register_deterministic()
    stoppers: list[Any] = []
    external_db = os.environ.get("TEST_DATABASE_URL")
    external_redis = os.environ.get("TEST_REDIS_URL")
    if external_db:
        base = external_db
    else:
        from testcontainers.postgres import PostgresContainer

        pg = PostgresContainer(POSTGRES_IMAGE, username="e", password="e", dbname="e")
        pg.start()
        stoppers.append(pg.stop)
        base = (
            f"postgresql+asyncpg://e:e@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/e"
        )
    if external_redis:
        redis_url = external_redis
    else:
        from testcontainers.redis import RedisContainer

        redis = RedisContainer(REDIS_IMAGE)
        redis.start()
        stoppers.append(redis.stop)
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}"
    storage = tempfile.mkdtemp(prefix="rag-eval-")
    name = f"eval_{uuid.uuid4().hex[:8]}"
    try:
        db_autocommit(base, f'CREATE DATABASE "{name}"')
        stoppers.append(
            lambda: db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        )
        url = make_url(base).set(database=name).render_as_string(hide_password=False)
        upgrade(url)
        values = settings_values(url, redis_url, storage)
        if profile == "models":
            for key in (
                "embedding_model_revision",
                "embedding_query_prefix",
                "embedding_passage_prefix",
                "rerank_preload",
            ):
                values.pop(key, None)
            values.update(
                embedding_provider="sentence_transformers",
                embedding_model="intfloat/multilingual-e5-small",
                embedding_allow_download=allow_download,
                rerank_provider="cross_encoder",
                rerank_model="BAAI/bge-reranker-v2-m3",
                rerank_model_revision="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
                rerank_allow_download=allow_download,
                rerank_preload=False,  # so the first rerank call measures a cold start
                rag_enabled=llm_model is not None,
            )
            values.update(
                llm_provider=llm_provider,
                llm_model=llm_model or "none-configured",
                llm_base_url=llm_base_url,
            )
        settings = Settings(_env_file=None, **values)
        os.environ["CELERY_BROKER_URL"] = settings.celery_broker_url
        os.environ["CELERY_RESULT_BACKEND"] = settings.celery_result_backend
        if profile == "models" and llm_model is not None:
            inner_factory = _FACTORIES[settings.llm_provider]
            register_llm_provider(
                settings.llm_provider,
                lambda config, key: RecordingLLM(inner_factory(config, key)),
            )
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            yield Stack(client, url, settings, profile, get_llm_provider(settings))
    finally:
        for stop in reversed(stoppers):
            stop()
        shutil.rmtree(storage, ignore_errors=True)


def make_user(stack: Stack, name: str) -> dict[str, str]:
    """Create a user (directly in the database) and log in; cached per stack and name."""
    if name in stack.users:
        return stack.users[name]
    email = f"{name}-{uuid.uuid4().hex[:8]}@eval.example.com"
    create_user_in_db(stack.database_url, email=email)
    response = stack.client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    stack.users[name] = {"Authorization": f"Bearer {token}"}
    return stack.users[name]
