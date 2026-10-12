"""Vector search latency and recall: exact per-user scan versus an HNSW index.

Run from `backend/` with Docker available (a throwaway pgvector PostgreSQL is started with
Testcontainers) or with TEST_DATABASE_URL pointing at a server that has the extension:

    python -m benchmarks.vector_search_baseline [--sizes 10000 100000] [--users 20]

The table mirrors the columns the real search filters and sorts on (`user_id`, `config_id`,
`embedding vector(384)`) without the foreign keys, so very large sizes load quickly. Vectors are
synthetic: unit vectors scattered around random cluster centres, which gives approximate-nearest-
neighbour search realistic structure; real documents will behave differently. Queries are
perturbed copies of stored vectors. Recall is measured against the exact result.
"""

import argparse
import asyncio
import os
import platform
import statistics
import sys
import time
import uuid

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.embeddings.constants import VECTOR_DIMENSION
from tests.helpers import db_autocommit

DIM = VECTOR_DIMENSION
TOP_K = 10
QUERIES = 100
SEED = 20261012


def synthetic(count: int, rng: np.random.Generator) -> np.ndarray:
    centres = rng.normal(size=(max(10, count // 200), DIM)).astype(np.float32)
    assignment = rng.integers(0, len(centres), size=count)
    vectors = centres[assignment] + 0.6 * rng.normal(size=(count, DIM)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors


def literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


async def load(
    conn: AsyncConnection, vectors: np.ndarray, users: list[uuid.UUID], config: uuid.UUID
) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS bench_vectors"))
    await conn.execute(
        text(
            f"CREATE TABLE bench_vectors (id serial PRIMARY KEY, user_id uuid NOT NULL, "
            f"config_id uuid NOT NULL, embedding vector({DIM}) NOT NULL)"
        )
    )
    batch = 2000
    for start in range(0, len(vectors), batch):
        rows = [
            {"u": str(users[(start + i) % len(users)]), "c": str(config), "v": literal(v)}
            for i, v in enumerate(vectors[start : start + batch])
        ]
        await conn.execute(
            text(
                "INSERT INTO bench_vectors (user_id, config_id, embedding) "
                "VALUES (:u, :c, CAST(:v AS vector))"
            ),
            rows,
        )
    await conn.execute(
        text("CREATE INDEX ix_bench_user_config ON bench_vectors (user_id, config_id)")
    )
    await conn.execute(text("ANALYZE bench_vectors"))


SEARCH = text(
    "SELECT id FROM bench_vectors WHERE user_id = :u AND config_id = :c "
    "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
)


async def run_queries(
    conn: AsyncConnection, queries: list[np.ndarray], user: uuid.UUID, config: uuid.UUID
) -> tuple[list[float], list[list[int]]]:
    times, results = [], []
    for q in queries:
        started = time.perf_counter()
        rows = (
            await conn.execute(
                SEARCH, {"u": str(user), "c": str(config), "q": literal(q), "k": TOP_K}
            )
        ).all()
        times.append((time.perf_counter() - started) * 1000)
        results.append([r[0] for r in rows])
    return times, results


def pct(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


async def explain(conn: AsyncConnection, user: uuid.UUID, config: uuid.UUID, q: np.ndarray) -> str:
    rows = (
        await conn.execute(
            text("EXPLAIN " + SEARCH.text),
            {"u": str(user), "c": str(config), "q": literal(q), "k": TOP_K},
        )
    ).all()
    plan = " ".join(r[0] for r in rows)
    return (
        "HNSW index"
        if "hnsw" in plan
        else ("btree + sort" if "ix_bench_user_config" in plan else "seq scan")
    )


async def benchmark(url: str, size: int, users_count: int) -> list[dict[str, object]]:
    rng = np.random.default_rng(SEED)
    vectors = synthetic(size, rng)
    users = [uuid.uuid4() for _ in range(users_count)]
    config = uuid.uuid4()
    engine = create_async_engine(url)
    rows: list[dict[str, object]] = []
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await load(conn, vectors, users, config)
            for label, user in (("one of many users", users[0]), ("single owner", None)):
                if user is None:
                    # re-assign every vector to one user to model a tenant owning the whole table
                    await conn.execute(
                        text("UPDATE bench_vectors SET user_id = :u"), {"u": str(users[0])}
                    )
                    await conn.execute(text("ANALYZE bench_vectors"))
                    user = users[0]
                owned = np.flatnonzero(
                    np.array([users[i % len(users)] == users[0] for i in range(size)])
                    if label == "one of many users"
                    else np.ones(size, dtype=bool)
                )
                pick = rng.choice(owned, size=QUERIES, replace=False)
                queries = []
                for index in pick:
                    noisy = vectors[index] + 0.1 * rng.normal(size=DIM).astype(np.float32)
                    queries.append(noisy / np.linalg.norm(noisy))

                await conn.execute(text("DROP INDEX IF EXISTS ix_bench_hnsw"))
                exact_times, exact = await run_queries(conn, queries, user, config)
                exact_plan = await explain(conn, user, config, queries[0])
                rows.append(
                    {
                        "size": size,
                        "scope": label,
                        "method": f"exact ({exact_plan})",
                        "build_s": 0.0,
                        "p50": statistics.median(exact_times),
                        "p95": pct(exact_times, 0.95),
                        "recall": 1.0,
                    }
                )

                started = time.perf_counter()
                await conn.execute(
                    text(
                        "CREATE INDEX ix_bench_hnsw ON bench_vectors USING hnsw "
                        "(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
                    )
                )
                build = time.perf_counter() - started
                await conn.execute(text("ANALYZE bench_vectors"))
                for ef in (40, 100, 200):
                    await conn.execute(text(f"SET hnsw.ef_search = {ef}"))
                    await conn.execute(text("SET hnsw.iterative_scan = relaxed_order"))
                    times, found = await run_queries(conn, queries, user, config)
                    plan = await explain(conn, user, config, queries[0])
                    recall = statistics.fmean(
                        len(set(f) & set(e)) / len(e) if e else 1.0
                        for f, e in zip(found, exact, strict=True)
                    )
                    rows.append(
                        {
                            "size": size,
                            "scope": label,
                            "method": f"hnsw ef={ef} iterative ({plan})",
                            "build_s": build,
                            "p50": statistics.median(times),
                            "p95": pct(times, 0.95),
                            "recall": recall,
                        }
                    )
                await conn.execute(text("RESET hnsw.ef_search"))
                await conn.execute(text("RESET hnsw.iterative_scan"))
    finally:
        await engine.dispose()
    return rows


def database_url() -> tuple[str, object]:
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
    name = f"vbench_{uuid.uuid4().hex[:8]}"
    db_autocommit(base, f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)

    def cleanup() -> None:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if container:
            container.stop()

    return url, cleanup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", nargs="+", type=int, default=[10_000, 100_000])
    parser.add_argument("--users", type=int, default=20)
    args = parser.parse_args()
    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    print(
        f"{DIM}-dimensional cosine, top_k={TOP_K}, {QUERIES} queries per row, {args.users} users\n"
    )
    url, cleanup = database_url()  # type: ignore[assignment]
    print("| Vectors | Scope | Method | Index build s | p50 ms | p95 ms | Recall@10 vs exact |")
    print("|---|---|---|---|---|---|---|")
    try:
        for size in args.sizes:
            for r in asyncio.run(benchmark(url, size, args.users)):
                print(
                    f"| {r['size']:,} | {r['scope']} | {r['method']} | {float(r['build_s']):.1f} | "
                    f"{float(r['p50']):.1f} | {float(r['p95']):.1f} | {float(r['recall']):.3f} |"
                )
                sys.stdout.flush()
    finally:
        cleanup()  # type: ignore[operator]


if __name__ == "__main__":
    main()
