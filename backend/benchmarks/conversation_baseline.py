# ruff: noqa: E501  (a report script: long print strings read better unwrapped)
"""Cost of persistent conversations through the real schema and the real HTTP stack (in process).

Run from `backend/` with Docker available (throwaway PostgreSQL and Redis through Testcontainers):

    python -m benchmarks.conversation_baseline [--real-llm qwen3.5:9b]

What is measured, with the fake generation provider unless `--real-llm` is given (so the numbers
are the application's own cost, not a model's):
- conversation list and message-history page latency over seeded data;
- database statements per turn (first turn, a turn with history) against single-turn `/rag/answer`;
- the latency the conversation layer and history loading add to single-turn RAG;
- the maximum configured history: prompt size against the model window;
- with `--real-llm`, input tokens and generation time of a single-turn answer against a fifth turn
  with history on a real local model.
Embeddings come from the deterministic test provider, so no embedding model is loaded.
Numbers are measured on the machine that runs this.
"""

import argparse
import asyncio
import os
import platform
import statistics
import sys
import time
import uuid
from typing import Any

# Importing the application builds a Celery app from the environment at import time.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://bench:bench@127.0.0.1:1/bench")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:1/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://127.0.0.1:1/2")
os.environ.setdefault("JWT_SECRET_KEY", "benchmark-only-secret-key-0123456789")

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.session import create_engine
from app.embeddings.registry import get_provider, register_provider
from app.llm.registry import register_llm_provider
from app.main import create_app
from evaluation.dataset import load_rag_corpus
from evaluation.retrieval_eval import load_and_embed
from tests.conftest import POSTGRES_IMAGE, REDIS_IMAGE
from tests.embedding_fakes import HashProvider
from tests.helpers import DEFAULT_PASSWORD, create_user_in_db, db_autocommit, db_execute, upgrade
from tests.llm_fakes import FakeLLM

SEED_CONVERSATIONS = 300
MESSAGES_PER_SEEDED_CONVERSATION = 6
LONG_CONVERSATION_MESSAGES = 400
QUESTION = "When are Aman rice seedlings transplanted?"
FOLLOW_UP = "and what about the spacing?"


def _ok(response: Any) -> None:
    if response.status_code not in (200, 201):
        raise RuntimeError(f"unexpected {response.status_code}: {response.text[:200]}")


def pct(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def summary(values: list[float]) -> str:
    return f"{statistics.median(values):.1f} / {pct(values, 0.95):.1f}"


def timed(call: Any, repeat: int) -> list[float]:
    times = []
    for _ in range(repeat):
        started = time.perf_counter()
        response = call()
        times.append((time.perf_counter() - started) * 1000)
        _ok(response)
    return times


class StatementCounter:
    def __init__(self, engine: Any) -> None:
        self.count = 0
        event.listen(engine, "before_cursor_execute", self._on)

    def _on(self, *args: Any) -> None:
        self.count += 1

    def around(self, call: Any) -> int:
        before = self.count
        response = call()
        _ok(response)
        return self.count - before


def seed(url: str, user: uuid.UUID) -> uuid.UUID:
    """Conversations with messages through SQL (fast), plus one long conversation."""
    db_execute(
        url,
        "INSERT INTO conversations (id, user_id, title, next_sequence, last_activity_at) "
        "SELECT gen_random_uuid(), CAST(:u AS uuid), 'seeded ' || g, :n + 1, "
        "now() - (g || ' minutes')::interval FROM generate_series(1, :c) g",
        u=str(user), n=MESSAGES_PER_SEEDED_CONVERSATION, c=SEED_CONVERSATIONS,
    )  # fmt: skip
    pairs = MESSAGES_PER_SEEDED_CONVERSATION // 2
    db_execute(
        url,
        "INSERT INTO messages (conversation_id, user_id, sequence, role, status, content) "
        "SELECT c.id, c.user_id, s, 'user', 'answered', 'seeded question ' || s "
        "FROM conversations c, generate_series(1, :p) p, LATERAL (SELECT p * 2 - 1 AS s) q "
        "WHERE c.title LIKE 'seeded %'",
        p=pairs,
    )
    db_execute(
        url,
        "INSERT INTO messages (conversation_id, user_id, sequence, role, status, content, "
        "reply_to_id, meta) "
        "SELECT q.conversation_id, q.user_id, q.sequence + 1, 'assistant', 'complete', "
        "'seeded answer', q.id, '{}'::jsonb FROM messages q WHERE q.role = 'user'",
    )
    long_id = uuid.uuid4()
    db_execute(
        url,
        "INSERT INTO conversations (id, user_id, title, next_sequence) "
        "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), 'long', :n + 1)",
        id=str(long_id), u=str(user), n=LONG_CONVERSATION_MESSAGES,
    )  # fmt: skip
    db_execute(
        url,
        "INSERT INTO messages (conversation_id, user_id, sequence, role, status, content) "
        "SELECT CAST(:id AS uuid), CAST(:u AS uuid), g * 2 - 1, 'user', 'answered', "
        "'long conversation question number ' || g || ' ' "
        "|| repeat('about rice and seedlings ', 20) "
        "FROM generate_series(1, :p) g",
        id=str(long_id), u=str(user), p=LONG_CONVERSATION_MESSAGES // 2,
    )  # fmt: skip
    db_execute(
        url,
        "INSERT INTO messages (conversation_id, user_id, sequence, role, status, content, "
        "reply_to_id, meta) "
        "SELECT q.conversation_id, q.user_id, q.sequence + 1, 'assistant', 'complete', "
        "'long answer ' || repeat('seedlings are planted in July ', 20), q.id, '{}'::jsonb "
        "FROM messages q WHERE q.conversation_id = CAST(:id AS uuid) AND q.role = 'user'",
        id=str(long_id),
    )
    return long_id


def run(real_llm: str | None) -> None:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer

    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    pg = PostgresContainer(POSTGRES_IMAGE, username="b", password="b", dbname="b")
    redis = RedisContainer(REDIS_IMAGE)
    pg.start()
    redis.start()
    name = f"convbench_{uuid.uuid4().hex[:8]}"
    base = f"postgresql+asyncpg://b:b@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/b"
    try:
        db_autocommit(base, f'CREATE DATABASE "{name}"')
        url = make_url(base).set(database=name).render_as_string(hide_password=False)
        upgrade(url)
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}"
        register_provider("test_hash", HashProvider)
        values: dict[str, Any] = {
            "database_url": url, "redis_url": f"{redis_url}/0",
            "celery_broker_url": f"{redis_url}/1", "celery_result_backend": f"{redis_url}/2",
            "jwt_secret_key": "benchmark-only-secret-key-0123456789", "rag_enabled": True,
            "embedding_provider": "test_hash", "embedding_model": "test-hash",
            "embedding_model_revision": "test-revision-1", "embedding_query_prefix": "",
            "embedding_passage_prefix": "", "search_max_top_k": 50,
            "embedding_task_budget_seconds": 3000, "embedding_stale_after_seconds": 7200,
            "rate_limit_enabled": False, "bcrypt_cost_factor": 4,
            "conversation_max_messages": 1000, "conversation_max_per_user": 100000,
            "llm_provider": "ollama" if real_llm else "test_llm",
            "llm_model": real_llm or "test-model", "llm_timeout_seconds": 300,
            "conversation_processing_stale_seconds": 1800,
            "llm_base_url": "http://127.0.0.1:11434",
        }  # fmt: skip
        register_llm_provider("test_llm", FakeLLM)
        settings = Settings(_env_file=None, **values)
        user = uuid.UUID(create_user_in_db(url, email="bench@example.com", cost=4))
        asyncio.run(_load(settings, user))
        long_id = seed(url, user)
        _measure(settings, long_id, real_llm)
    finally:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        pg.stop()
        redis.stop()


async def _load(settings: Settings, user: uuid.UUID) -> None:
    engine = create_engine(settings)
    try:
        provider = get_provider(settings)
        provider.warmup()
        await load_and_embed(
            settings, provider, async_sessionmaker(engine, expire_on_commit=False),
            load_rag_corpus(), user,
        )  # fmt: skip
    finally:
        await engine.dispose()


def _measure(settings: Settings, long_id: uuid.UUID, real_llm: str | None) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login", json={"email": "bench@example.com", "password": DEFAULT_PASSWORD}
        ).json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        counter = StatementCounter(app.state.engine.sync_engine)
        base = "/api/v1/conversations"

        def get(path: str, **params: Any) -> Any:
            return client.get(path, headers=headers, params=params)

        def new_conversation() -> str:
            return str(client.post(base, headers=headers, json={}).json()["data"]["id"])

        def ask(conversation: str, text: str) -> Any:
            return client.post(
                f"{base}/{conversation}/messages", headers=headers, json={"content": text}
            )

        print(
            f"\n## Seeded data: {SEED_CONVERSATIONS} conversations of {MESSAGES_PER_SEEDED_CONVERSATION}"
            f" messages, one of {LONG_CONVERSATION_MESSAGES}"
        )
        print("\n| Call | median / p95 ms |\n|---|---|")
        for label, call in (
            ("list conversations, page 1 (20)", lambda: get(base, page=1, page_size=20)),
            ("list conversations, page 15 (20)", lambda: get(base, page=15, page_size=20)),
            ("list conversations, 100 per page", lambda: get(base, page_size=100)),
            ("history page 1 of 400 messages (20)", lambda: get(f"{base}/{long_id}", page_size=20)),
            ("history page 10 of 400 (20)", lambda: get(f"{base}/{long_id}", page=10, page_size=20)),
            ("history newest first (20)", lambda: get(f"{base}/{long_id}", order="desc", page_size=20)),
            ("history 100 per page", lambda: get(f"{base}/{long_id}", page_size=100)),
        ):  # fmt: skip
            print(f"| {label} | {summary(timed(call, 40))} |")

        print("\n## Database statements per request\n\n| Request | statements |\n|---|---:|")
        answer = lambda: client.post(  # noqa: E731
            "/api/v1/rag/answer", headers=headers, json={"question": QUESTION}
        )
        print(f"| single-turn POST /rag/answer | {counter.around(answer)} |")
        first = new_conversation()
        print(
            f"| conversation turn 1 (no history) | {counter.around(lambda: ask(first, QUESTION))} |"
        )
        print(
            f"| conversation turn 2 (with history) | {counter.around(lambda: ask(first, FOLLOW_UP))} |"
        )
        for _ in range(3):
            ask(first, FOLLOW_UP)
        print(
            f"| conversation turn 6 (4 history turns) | {counter.around(lambda: ask(first, FOLLOW_UP))} |"
        )
        print(
            f"| GET a 400-message page | {counter.around(lambda: get(f'{base}/{long_id}', page_size=20))} |"
        )
        print(f"| GET list | {counter.around(lambda: get(base))} |")

        repeat = 5 if real_llm else 25
        print(
            f"\n## Latency of one answer ({'real model ' + real_llm if real_llm else 'fake provider'})\n"
        )
        print("| Request | median / p95 ms |\n|---|---|")
        single = timed(answer, repeat)
        print(f"| single-turn /rag/answer | {summary(single)} |")

        def first_turn() -> Any:
            return ask(new_conversation(), QUESTION)

        print(
            f"| conversation turn 1 (a new conversation each time: includes creating it) | "
            f"{summary(timed(first_turn, repeat))} |"
        )
        c = new_conversation()
        ask(c, QUESTION)
        for _ in range(3):
            ask(c, FOLLOW_UP)
        # keep the conversation at four completed turns: later turns also have four history turns
        print(
            f"| conversation turn with 4 history turns | {summary(timed(lambda: ask(c, FOLLOW_UP), repeat))} |"
        )

        print("\n## Prompt size at the configured maximum history\n")
        provider = app.state.settings
        print(
            f"model window {provider.llm_context_tokens} tokens, history budget "
            f"{provider.conversation_history_token_budget}, turns {provider.conversation_history_turns}"
        )
        response = ask(str(long_id), FOLLOW_UP)
        details = response.json()["data"]["assistant_message"]["details"]
        generation = details["generation"]
        print(
            f"- 400-message conversation, next question: history turns used {details['history_turns']},"
            f" provider input tokens {generation['input_tokens']}, "
            f"estimated context tokens {details['context']['estimated_input_tokens']},"
            f" total {response.elapsed.total_seconds() * 1000:.0f} ms"
        )
        if real_llm:
            plain = client.post("/api/v1/rag/answer", headers=headers, json={"question": QUESTION})
            print(
                f"- real model: single-turn input tokens "
                f"{plain.json()['data']['generation']['input_tokens']}, generation "
                f"{plain.json()['data']['generation']['latency_ms']} ms; with history input tokens "
                f"{generation['input_tokens']}, generation {generation['latency_ms']} ms"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--real-llm", help="Ollama model name (default: a fake provider)")
    args = parser.parse_args()
    run(args.real_llm)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
