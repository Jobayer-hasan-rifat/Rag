# ruff: noqa: E501  (long SQL strings and report rows; a benchmark script, not application code)
"""Embedding and vector search benchmarks for the configured model.

Run from `backend/` with the ML dependencies installed (and Docker for the search part):

    python -m benchmarks.embedding_baseline                    # everything
    python -m benchmarks.embedding_baseline --skip-search      # model only
    python -m benchmarks.embedding_baseline --device cuda      # force a device
    python -m benchmarks.embedding_baseline --cold             # include a first-download timing

Measures, on the machine that runs it:
- model start-up: warm (files cached) and, with --cold, a first download into a temporary cache;
- embedding throughput for English, Bangla and mixed text of about 1,000 characters (the chunk
  size) at several batch sizes, median of three runs after a warm-up;
- query embedding latency;
- vector search latency through the real schema (`EmbeddingRepository.search`: ownership filter,
  document and section joins) with synthetic vectors at documented sizes.
Nothing here is quoted from model cards. A GPU is used only if one is present; otherwise the
report says so.
"""

import argparse
import asyncio
import os
import platform
import statistics
import sys
import tempfile
import time
import uuid
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.embeddings.constants import VECTOR_DIMENSION
from app.embeddings.registry import embedding_config, get_provider, reset_provider_cache
from benchmarks.chunking_eval import (
    BANGLA_SENTENCES,
    ENGLISH_SENTENCES,
    MIXED_SENTENCES,
    _paragraphs,
)
from tests.helpers import create_user_in_db, db_autocommit, db_execute, upgrade

REPEATS = 3
BATCH_SIZES = (1, 4, 8, 16, 32, 64)
CHUNKS_PER_TYPE = 128
SEED = 20261012


def settings_for(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://x:x@localhost/x",
        "redis_url": "redis://localhost",
        "celery_broker_url": "redis://localhost",
        "celery_result_backend": "redis://localhost",
        "jwt_secret_key": "x" * 40,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def text_chunks(bank: list[str], count: int, seed: int) -> list[str]:
    """`count` texts of about 1,000 characters built from the sentence bank."""
    out: list[str] = []
    for i in range(count):
        raw = _paragraphs(bank, 1, seed=seed + i, max_sentences=40)
        while len(raw) < 1000:
            raw += " " + _paragraphs(bank, 1, seed=seed + i + 10_000, max_sentences=9)
        out.append(raw[:1000])
    return out


def pct(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


# --- model -----------------------------------------------------------------------------------


def bench_startup(settings: Settings, cold: bool) -> dict[str, float]:
    import torch

    result: dict[str, float] = {}
    if cold:
        with tempfile.TemporaryDirectory() as cache:
            cold_settings = settings.model_copy(
                update={"embedding_cache_dir": cache, "embedding_allow_download": True}
            )
            reset_provider_cache()
            started = time.perf_counter()
            get_provider(cold_settings).warmup()
            result["cold_download_and_load_s"] = time.perf_counter() - started
    warm = []
    for _ in range(3):
        reset_provider_cache()
        started = time.perf_counter()
        get_provider(settings).warmup()
        warm.append(time.perf_counter() - started)
    result["warm_load_s"] = statistics.median(warm)
    result["torch_threads"] = float(torch.get_num_threads())
    return result


def bench_throughput(settings: Settings) -> list[dict[str, Any]]:
    corpora = {
        "English": text_chunks(ENGLISH_SENTENCES, CHUNKS_PER_TYPE, SEED),
        "Bangla": text_chunks(BANGLA_SENTENCES, CHUNKS_PER_TYPE, SEED + 1),
        "Mixed": text_chunks(MIXED_SENTENCES, CHUNKS_PER_TYPE, SEED + 2),
    }
    rows: list[dict[str, Any]] = []
    for batch_size in BATCH_SIZES:
        provider = get_provider(settings.model_copy(update={"embedding_batch_size": batch_size}))
        provider.warmup()
        for name, texts in corpora.items():
            provider.embed_documents(texts[: min(8, len(texts))])  # warm-up, excluded
            times, truncated = [], 0
            for _ in range(REPEATS):
                started = time.perf_counter()
                batch = provider.embed_documents(texts)
                times.append(time.perf_counter() - started)
                truncated = batch.truncated_count
            seconds = statistics.median(times)
            rows.append(
                {"text": name, "batch": batch_size, "chunks_per_s": len(texts) / seconds,
                 "chars_per_s": sum(map(len, texts)) / seconds, "truncated": truncated}
            )  # fmt: skip
    return rows


def bench_queries(settings: Settings) -> dict[str, float]:
    provider = get_provider(settings)
    provider.warmup()
    queries = [
        "How long can submerged rice plants survive in floodwater?",
        "ইলিশ ধরা কবে বন্ধ থাকে?",
        "Query slow হলে কী করা উচিত?",
        "২১শে ফেব্রুয়ারি — মাতৃভাষা দিবস কেন?",
    ]
    provider.embed_query(queries[0])
    times = []
    for i in range(200):
        started = time.perf_counter()
        provider.embed_query(queries[i % len(queries)])
        times.append((time.perf_counter() - started) * 1000)
    return {"p50": statistics.median(times), "p95": pct(times, 0.95), "max": max(times)}


# --- vector search through the real schema ---------------------------------------------------


async def load_chunks(
    engine: AsyncEngine, owners: list[str], total: int, users: int, per_doc: int
) -> dict[str, Any]:
    docs = total // per_doc
    async with engine.begin() as conn:
        for index in range(users):
            await conn.execute(
                text(
                    "INSERT INTO documents (id, user_id, filename, storage_key, file_type, content_type, "
                    "file_size, checksum_sha256, status, chunk_count, chunking_version) "
                    "SELECT gen_random_uuid(), CAST(:u AS uuid), 'b' || g || '.txt', 'documents/bb/' || md5(CAST(:u AS text) || g::text), "
                    "'txt', 'text/plain', 1, md5(CAST(:u AS text) || g::text) || md5(g::text), 'ready', :per, 'bench' "
                    "FROM generate_series(1, :n) g"
                ),
                {"u": owners[index], "per": per_doc, "n": docs // users},
            )
        await conn.execute(
            text(
                "INSERT INTO document_sections (id, document_id, ordinal, kind, text, char_count) "
                "SELECT gen_random_uuid(), id, 0, 'body', 'x', 1 FROM documents WHERE chunking_version = 'bench'"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO document_chunks (id, document_id, section_id, chunking_version, chunk_index, "
                "text, char_count, text_sha256, start_char, end_char, heading_path) "
                "SELECT gen_random_uuid(), d.id, s.id, 'bench', i, repeat('word ', 200), 1000, "
                "md5(d.id::text || i::text) || md5(i::text), 0, 1000, ARRAY['Bench'] "
                "FROM documents d JOIN document_sections s ON s.document_id = d.id "
                "CROSS JOIN generate_series(0, :per - 1) i WHERE d.chunking_version = 'bench'"
            ),
            {"per": per_doc},
        )
    return {"owners": owners}


async def insert_vectors(engine: AsyncEngine, vectors: np.ndarray, config_id: uuid.UUID) -> None:
    async with engine.begin() as conn:
        ids = (
            await conn.execute(
                text(
                    "SELECT c.id, c.document_id, d.user_id FROM document_chunks c "
                    "JOIN documents d ON d.id = c.document_id WHERE c.chunking_version = 'bench' ORDER BY c.id"
                )
            )
        ).all()
    batch = 2000
    for start in range(0, len(ids), batch):
        rows = [
            {"c": str(r[0]), "d": str(r[1]), "u": str(r[2]), "cfg": str(config_id),
             "v": "[" + ",".join(f"{x:.5f}" for x in vectors[start + i]) + "]"}
            for i, r in enumerate(ids[start : start + batch])
        ]  # fmt: skip
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO chunk_embeddings (chunk_id, document_id, user_id, config_id, embedding, text_sha256) "
                    "VALUES (:c, :d, :u, :cfg, CAST(:v AS vector), repeat('a', 64))"
                ),
                rows,
            )


async def bench_search(url: str, owners: list[str], total: int, users: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(SEED)
    engine = create_async_engine(url)
    try:
        loaded = await load_chunks(engine, owners, total, users, per_doc=100)
        async with engine.begin() as conn:
            config_id = (
                await conn.execute(
                    text(
                        "INSERT INTO embedding_configs (fingerprint, provider, model, revision, dimension, metric, "
                        "normalized, max_seq_length, include_heading, preprocessing_version) "
                        "VALUES (:f, 'bench', 'bench', 'bench', 384, 'cosine', true, 512, false, 'e1') RETURNING id"
                    ),
                    {"f": uuid.uuid4().hex * 2},
                )
            ).scalar_one()
        centres = rng.normal(size=(max(10, total // 200), VECTOR_DIMENSION)).astype(np.float32)
        vectors = centres[rng.integers(0, len(centres), size=total)] + 0.6 * rng.normal(
            size=(total, VECTOR_DIMENSION)
        ).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        await insert_vectors(engine, vectors, config_id)
        async with engine.begin() as conn:
            await conn.execute(text("ANALYZE chunk_embeddings"))
            await conn.execute(text("ANALYZE document_chunks"))
            await conn.execute(text("ANALYZE documents"))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.UUID(loaded["owners"][0])
        times: list[float] = []
        for index in rng.choice(total, size=100, replace=False):
            noisy = vectors[index] + 0.1 * rng.normal(size=VECTOR_DIMENSION).astype(np.float32)
            query = (noisy / np.linalg.norm(noisy)).tolist()
            async with factory() as session:
                started = time.perf_counter()
                await EmbeddingRepository(session).search(
                    user_id=user_id, config_id=config_id, vector=query, top_k=10
                )
                times.append((time.perf_counter() - started) * 1000)
        async with engine.begin() as conn:
            plan = " ".join(
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "EXPLAIN SELECT chunk_id FROM chunk_embeddings WHERE user_id = :u AND config_id = :c "
                            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 10"
                        ),
                        {
                            "u": str(user_id),
                            "c": str(config_id),
                            "q": "[" + ",".join(["0.05"] * VECTOR_DIMENSION) + "]",
                        },
                    )
                ).all()
            )
        mine = total // users
        return [{"total": total, "users": users, "scope": f"one user ({mine:,} vectors)",
                 "p50": statistics.median(times[5:]), "p95": pct(times[5:], 0.95),
                 "plan": "index on (user_id, config_id) + sort" if "ix_chunk_embeddings_user_config" in plan else "other"}]  # fmt: skip
    finally:
        await engine.dispose()


def database() -> tuple[str, Any]:
    from testcontainers.postgres import PostgresContainer

    from tests.conftest import POSTGRES_IMAGE

    external = os.environ.get("TEST_DATABASE_URL")
    container = None
    if external:
        base = external
    else:
        container = PostgresContainer(
            POSTGRES_IMAGE, username="bench", password="bench", dbname="bench"
        )
        container.start()
        base = (
            f"postgresql+asyncpg://bench:bench@{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(5432)}/bench"
        )
    name = f"ebench_{uuid.uuid4().hex[:8]}"
    db_autocommit(base, f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    upgrade(url)

    def cleanup() -> None:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if container:
            container.stop()

    return url, cleanup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--skip-search", action="store_true")
    parser.add_argument("--skip-model", action="store_true", help="only run the search part")
    parser.add_argument("--sizes", nargs="+", type=int, default=[10_000, 50_000])
    parser.add_argument("--users", type=int, default=20)
    args = parser.parse_args()

    import torch

    settings = settings_for(embedding_device=args.device)
    config = embedding_config(settings)
    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    print(
        f"torch {torch.__version__}, cuda available: {torch.cuda.is_available()}, threads {torch.get_num_threads()}"
    )
    print(
        f"model {config.model} @ {config.revision} ({config.dimension} dims), device={args.device}\n"
    )
    if not torch.cuda.is_available() and args.device in ("auto", "cuda"):
        print("No CUDA device on this machine: GPU throughput was NOT measured.\n")

    if not args.skip_model:
        startup = bench_startup(settings, args.cold)
        print("Model start-up:")
        for key, value in startup.items():
            print(f"- {key}: {value:.1f}")
        print(
            "\n| Text (1,000 chars) | Batch | Chunks/s | Chars/s | Truncated |\n|---|---|---|---|---|"
        )
        for r in bench_throughput(settings):
            print(
                f"| {r['text']} | {r['batch']} | {r['chunks_per_s']:.1f} | {r['chars_per_s']:,.0f} | {r['truncated']} |"
            )
        queries = bench_queries(settings)
        print(
            f"\nQuery embedding latency (200 queries): p50 {queries['p50']:.1f} ms, p95 {queries['p95']:.1f} ms, max {queries['max']:.1f} ms"
        )
        sys.stdout.flush()

    if args.skip_search:
        return
    url, cleanup = database()
    try:
        print(
            "\n| Vectors in table | Users | Search scope | p50 ms | p95 ms | Plan |\n|---|---|---|---|---|---|"
        )
        for size in args.sizes:
            for users in (1, args.users):
                owners = [
                    create_user_in_db(url, email=f"b{i}-{uuid.uuid4().hex[:6]}@example.com")
                    for i in range(users)
                ]
                for r in asyncio.run(bench_search(url, owners, size, users)):
                    print(
                        f"| {r['total']:,} | {r['users']} | {r['scope']} | {r['p50']:.1f} | {r['p95']:.1f} | {r['plan']} |"
                    )
                    sys.stdout.flush()
                db_execute(url, "TRUNCATE users CASCADE")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
