# ruff: noqa: E501  (SQL strings and report rows in a benchmark script)
"""Which index layout should lexical search use? Plain GIN versus a per-user composite GIN.

Run from `backend/` with Docker available:  python -m benchmarks.sparse_index_probe

Builds a raw table of synthetic chunks (about 1,000 characters of generated Bangla/English/mixed
text each, with a few planted rare terms) for several users, then times the same IDF-weighted
lexical query under three layouts:
  A. no index (sequential scan, for scale),
  B. GIN(search_vector) alone, ownership applied by joining the user's documents,
  C. GIN(user_id, search_vector) through btree_gin, ownership applied inside the index scan.
The data is synthetic; absolute numbers depend on the machine.
"""

import argparse
import asyncio
import os
import platform
import random
import statistics
import time
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from benchmarks.chunking_eval import BANGLA_SENTENCES, ENGLISH_SENTENCES, MIXED_SENTENCES
from tests.helpers import db_autocommit

SEED = 20261014
COMMON = "report"  # appears in a large share of the chunks
RARE = ["kestrel9", "ERR-4721", "তিস্তা", "ময়নামতি"]


def make_texts(count: int) -> list[str]:
    rng = random.Random(SEED)
    banks = [ENGLISH_SENTENCES, BANGLA_SENTENCES, MIXED_SENTENCES]
    texts = []
    for i in range(count):
        bank = banks[i % 3]
        parts = [rng.choice(bank) for _ in range(rng.randint(5, 9))]
        if rng.random() < 0.4:
            parts.insert(rng.randrange(len(parts)), COMMON)
        if rng.random() < 0.002:
            parts.insert(rng.randrange(len(parts)), rng.choice(RARE))
        texts.append(" ".join(parts)[:1000])
    return texts


SPARSE = text("""
WITH terms AS (
    SELECT unnest(tsvector_to_array(to_tsvector('simple', :q))) AS lexeme
), ready AS (
    SELECT id FROM bench_docs WHERE user_id = :u
), df AS (
    SELECT t.lexeme,
           (SELECT count(*) FROM bench_chunks c
             WHERE c.user_id = :u AND c.search_vector @@ phraseto_tsquery('simple', t.lexeme)) AS n
    FROM terms t
), total AS (SELECT count(*)::float AS n FROM bench_chunks WHERE user_id = :u),
w AS (
    SELECT df.lexeme, ln(1 + (total.n - df.n + 0.5) / (df.n + 0.5)) AS idf
    FROM df, total WHERE df.n > 0 ORDER BY 2 DESC LIMIT 12
)
SELECT c.id, sum(w.idf) AS score
FROM w JOIN bench_chunks c
  ON c.user_id = :u AND c.search_vector @@ phraseto_tsquery('simple', w.lexeme)
GROUP BY c.id ORDER BY score DESC, c.id LIMIT 50
""")

# the same query with ownership applied through the user's documents instead of a column
SPARSE_JOIN = text(
    SPARSE.text.replace("c.user_id = :u AND", "c.doc_id IN (SELECT id FROM ready) AND").replace(
        "WHERE c.user_id = :u AND c.search_vector",
        "WHERE c.doc_id IN (SELECT id FROM ready) AND c.search_vector",
    )
)


async def load(conn: AsyncConnection, total: int, users: int) -> list[uuid.UUID]:
    owners = [uuid.uuid4() for _ in range(users)]
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
    for name in ("bench_chunks", "bench_docs"):
        await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
    await conn.execute(text("CREATE TABLE bench_docs (id uuid PRIMARY KEY, user_id uuid NOT NULL)"))
    await conn.execute(
        text(
            "CREATE TABLE bench_chunks (id uuid PRIMARY KEY, doc_id uuid NOT NULL, user_id uuid NOT NULL, "
            "body text NOT NULL, search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', body)) STORED)"
        )
    )
    docs_per_user = max(1, total // users // 100)
    doc_ids = []
    for owner in owners:
        for _ in range(docs_per_user):
            doc = uuid.uuid4()
            doc_ids.append((doc, owner))
    await conn.execute(
        text("INSERT INTO bench_docs (id, user_id) VALUES (:d, :u)"),
        [{"d": str(d), "u": str(u)} for d, u in doc_ids],
    )
    texts = make_texts(total)
    batch = 2000
    for start in range(0, total, batch):
        rows = []
        for i, body in enumerate(texts[start : start + batch]):
            doc, owner = doc_ids[(start + i) % len(doc_ids)]
            rows.append({"i": str(uuid.uuid4()), "d": str(doc), "u": str(owner), "b": body})
        await conn.execute(
            text("INSERT INTO bench_chunks (id, doc_id, user_id, body) VALUES (:i, :d, :u, :b)"),
            rows,
        )
    await conn.execute(text("CREATE INDEX ix_bench_chunks_doc ON bench_chunks (doc_id)"))
    await conn.execute(text("ANALYZE bench_chunks"))
    await conn.execute(text("ANALYZE bench_docs"))
    return owners


async def time_queries(
    conn: AsyncConnection, statement: object, user: uuid.UUID, queries: list[str]
) -> tuple[float, float]:
    times = []
    for query in queries * 5:
        started = time.perf_counter()
        await conn.execute(statement, {"q": query, "u": str(user)})  # type: ignore[call-overload]
        times.append((time.perf_counter() - started) * 1000)
    times = times[len(queries) :]  # drop the first pass (cold caches)
    return statistics.median(times), sorted(times)[int(len(times) * 0.95) - 1]


async def run(url: str, total: int, users: int) -> None:
    engine = create_async_engine(url)
    queries = [
        "kestrel9",  # rare identifier
        "What does error ERR-4721 mean for the report?",  # rare term among common words
        "ইলিশ ধরা কবে নিষিদ্ধ থাকে?",  # Bangla question
        "How do I report the quarterly numbers?",  # mostly common words
        "তিস্তা ময়নামতি",  # rare Bangla terms
    ]
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            owners = await load(conn, total, users)
            mine = (
                await conn.execute(
                    text("SELECT count(*) FROM bench_chunks WHERE user_id = :u"),
                    {"u": str(owners[0])},
                )
            ).scalar_one()
            print(f"\n{total:,} chunks, {users} users, the searching user owns {mine:,}")
            print("| Layout | p50 ms | p95 ms | index size |\n|---|---|---|---|")
            rows = []
            m, p = await time_queries(conn, SPARSE_JOIN, owners[0], queries)
            rows.append(("A. no index (scan)", m, p, "-"))
            await conn.execute(
                text("CREATE INDEX ix_bench_gin ON bench_chunks USING gin (search_vector)")
            )
            await conn.execute(text("ANALYZE bench_chunks"))
            size = (
                await conn.execute(text("SELECT pg_size_pretty(pg_relation_size('ix_bench_gin'))"))
            ).scalar_one()
            m, p = await time_queries(conn, SPARSE_JOIN, owners[0], queries)
            rows.append(("B. GIN(search_vector) + join user's documents", m, p, size))
            await conn.execute(text("DROP INDEX ix_bench_gin"))
            await conn.execute(
                text(
                    "CREATE INDEX ix_bench_gin_user ON bench_chunks USING gin (user_id, search_vector)"
                )
            )
            await conn.execute(text("ANALYZE bench_chunks"))
            size = (
                await conn.execute(
                    text("SELECT pg_size_pretty(pg_relation_size('ix_bench_gin_user'))")
                )
            ).scalar_one()
            m, p = await time_queries(conn, SPARSE, owners[0], queries)
            rows.append(("C. GIN(user_id, search_vector) via btree_gin", m, p, size))
            for name, m, p, size in rows:
                print(f"| {name} | {m:.1f} | {p:.1f} | {size} |")
            plan = (
                await conn.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + SPARSE.text),
                    {"q": "kestrel9", "u": str(owners[0])},
                )
            ).all()
            print("\nPlan for layout C, rare term:")
            for row in plan[:14]:
                print("  " + row[0][:150])
    finally:
        await engine.dispose()


def database() -> tuple[str, object]:
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
        base = f"postgresql+asyncpg://bench:bench@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}/bench"
    name = f"sbench_{uuid.uuid4().hex[:8]}"
    db_autocommit(base, f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)

    def cleanup() -> None:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if container:
            container.stop()

    return url, cleanup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", nargs="+", type=int, default=[20_000, 100_000])
    parser.add_argument("--users", type=int, default=20)
    args = parser.parse_args()
    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    url, cleanup = database()
    try:
        for size in args.sizes:
            asyncio.run(run(url, size, args.users))
    finally:
        cleanup()  # type: ignore[operator]


if __name__ == "__main__":
    main()
