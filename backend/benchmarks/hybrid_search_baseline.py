"""Dense, sparse and hybrid candidate generation latency through the real schema.

Run from `backend/` with Docker available (a throwaway pgvector PostgreSQL is started with
Testcontainers) or with TEST_DATABASE_URL pointing at a database server:

    python -m benchmarks.hybrid_search_baseline [--sizes 10000 100000] [--users 20]

Unlike `vector_search_baseline` this loads the actual tables (users, documents, sections, chunks,
embeddings) by running the migrations, so the 0007 trigger, composite foreign keys, generated
`search_vector` column and GIN index are all in play. The repositories the API uses are timed:
exact vector search (`EmbeddingRepository.nearest_candidates`), lexical search
(`LexicalRepository.candidates`), hydration of the fused result and the fusion itself. The model
is not involved: query vectors are synthetic. Text is synthetic too: words drawn from a Zipf-like
vocabulary, so a few words are very common and most are rare; real language will differ. Numbers
are measured on the machine that runs this.
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

import numpy as np
from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.repositories.lexical_repository import _SQL, LexicalRepository
from app.embeddings.constants import VECTOR_DIMENSION
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical_query import parse_lexical_query
from app.retrieval.stopwords import STOPWORDS
from tests.helpers import create_user_in_db, db_autocommit, upgrade

CHUNKS_PER_DOCUMENT = 50
WORDS_PER_CHUNK = 40
VOCABULARY = 20_000
QUERIES = 100
CANDIDATES = 50
SEED = 20261013

POPULATE = """
INSERT INTO documents (id, user_id, filename, storage_key, file_type, content_type, file_size,
                       checksum_sha256, status, chunk_count, chunking_version)
SELECT md5(:ut || 'd' || g)::uuid, CAST(:u AS uuid), 'bench-' || g || '.txt',
       'bench/' || :ut || '/' || g, 'txt', 'text/plain', 1, md5(:ut || g) || md5(g::text),
       'ready', :per, 'bench'
FROM generate_series(1, :docs) g;

INSERT INTO document_sections (id, document_id, ordinal, kind, heading, heading_level, text,
                               char_count)
SELECT md5(:ut || 's' || d || '-' || c)::uuid, md5(:ut || 'd' || d)::uuid, c - 1, 'section', 'h', 2,
       'x', 1
FROM generate_series(1, :docs) d, generate_series(1, :per) c;

INSERT INTO document_chunks (id, document_id, section_id, chunking_version, chunk_index, text,
                             char_count, text_sha256, start_char, end_char, overlap_chars)
SELECT md5(:ut || 'c' || d || '-' || c)::uuid, md5(:ut || 'd' || d)::uuid,
       md5(:ut || 's' || d || '-' || c)::uuid, 'bench', c - 1, t.body, char_length(t.body),
       md5(:ut || 'c' || d || '-' || c) || md5(c::text), 0, char_length(t.body), 0
FROM generate_series(1, :docs) d, generate_series(1, :per) c,
LATERAL (
    SELECT string_agg('w' || floor(power(random(), 3) * :vocab)::int, ' ') AS body
    FROM generate_series(1, :words)
) t;

INSERT INTO chunk_embeddings (chunk_id, document_id, user_id, config_id, embedding, text_sha256)
SELECT c.id, c.document_id, c.user_id, CAST(:cfg AS uuid), v.embedding, c.text_sha256
FROM document_chunks c,
LATERAL (
    SELECT (array_agg(random()::real))::vector AS embedding FROM generate_series(1, :dim)
) v
WHERE c.user_id = CAST(:u AS uuid)
"""


def _pct(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def _summary(values: list[float]) -> str:
    return f"{statistics.median(values):.1f} / {_pct(values, 0.95):.1f}"


async def _populate(url: str, users: list[uuid.UUID], size: int, config: uuid.UUID) -> float:
    engine = create_async_engine(url)
    per_user = size // len(users)
    docs = max(1, per_user // CHUNKS_PER_DOCUMENT)
    started = time.perf_counter()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE documents CASCADE"))
            await conn.execute(
                text(
                    "INSERT INTO embedding_configs (id, fingerprint, provider, model, revision, "
                    "dimension, metric, normalized, max_seq_length, include_heading, "
                    "preprocessing_version) VALUES (CAST(:c AS uuid), :f, 'bench', 'bench', "
                    "'r', :dim, 'cosine', false, 512, false, '1') ON CONFLICT DO NOTHING"
                ),
                {"c": str(config), "f": config.hex * 2, "dim": VECTOR_DIMENSION},
            )
        for user in users:
            async with engine.begin() as conn:
                params = {
                    "u": str(user), "ut": str(user), "docs": docs, "per": CHUNKS_PER_DOCUMENT,
                    "vocab": VOCABULARY, "words": WORDS_PER_CHUNK, "cfg": str(config),
                    "dim": VECTOR_DIMENSION,
                }  # fmt: skip
                for statement in POPULATE.split(";\n\n"):
                    await conn.execute(text(statement), params)
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            for table in ("document_chunks", "chunk_embeddings", "documents"):
                await conn.execute(text(f"ANALYZE {table}"))
    finally:
        await engine.dispose()
    return time.perf_counter() - started


def _random_query_words(rng: np.random.Generator) -> str:
    """Two frequent-ish words and one rare one, like a real question with a distinctive term."""
    common = [f"w{int(VOCABULARY * rng.random() ** 3)}" for _ in range(2)]
    rare = f"w{int(rng.integers(VOCABULARY // 2, VOCABULARY))}"
    return " ".join([*common, rare])


async def _explain_lexical(session: AsyncSession, user: uuid.UUID, words: str) -> str:
    statement = text(
        "EXPLAIN (ANALYZE, BUFFERS) " + _SQL.format(doc_filter="", collection_filter="")
    ).bindparams(
        bindparam("phrases", type_=ARRAY(Text)),
        bindparam("stopwords", type_=ARRAY(Text)),
        bindparam("u", type_=UUID(as_uuid=True)),
    )
    rows = await session.execute(
        statement,
        {
            "u": user, "words": words, "phrases": [], "stopwords": sorted(STOPWORDS),
            "max_terms": 12, "scan_terms": 36, "limit": CANDIDATES,
        },
    )  # fmt: skip
    return "\n".join(row[0] for row in rows)


async def _measure(url: str, users: list[uuid.UUID], config: uuid.UUID) -> dict[str, Any]:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rng = np.random.default_rng(SEED)
    series: dict[str, list[float]] = {
        k: [] for k in ("dense", "sparse", "hydrate", "fuse", "hybrid_total")
    }
    explain = ""
    try:
        async with factory() as session:
            dense_repo, lexical_repo = EmbeddingRepository(session), LexicalRepository(session)
            for i in range(QUERIES + 5):
                user = users[i % len(users)]
                vector = rng.normal(size=VECTOR_DIMENSION).astype(np.float32).tolist()
                words = _random_query_words(rng)
                started = time.perf_counter()
                dense = await dense_repo.nearest_candidates(
                    user_id=user, config_id=config, vector=vector, limit=CANDIDATES
                )
                t_dense = time.perf_counter()
                sparse = await lexical_repo.candidates(
                    user_id=user, query=parse_lexical_query(words), limit=CANDIDATES,
                    max_terms=12, stopwords=STOPWORDS,
                )  # fmt: skip
                t_sparse = time.perf_counter()
                fused = reciprocal_rank_fusion(
                    {"dense": [c.chunk_id for c in dense], "sparse": [c.chunk_id for c in sparse]},
                    limit=10,
                )
                t_fuse = time.perf_counter()
                await dense_repo.hydrate(user_id=user, chunk_ids=[f.key for f in fused])
                finished = time.perf_counter()
                await session.rollback()
                if i < 5:  # warm-up
                    continue
                series["dense"].append((t_dense - started) * 1000)
                series["sparse"].append((t_sparse - t_dense) * 1000)
                series["fuse"].append((t_fuse - t_sparse) * 1000)
                series["hydrate"].append((finished - t_fuse) * 1000)
                series["hybrid_total"].append((finished - started) * 1000)
            explain = await _explain_lexical(session, users[0], _random_query_words(rng))
            await session.rollback()
        async with engine.connect() as conn:
            sizes = (
                await conn.execute(
                    text(
                        "SELECT pg_size_pretty(pg_relation_size('document_chunks')), "
                        "pg_size_pretty(pg_relation_size('ix_document_chunks_search')), "
                        "pg_size_pretty(pg_relation_size('chunk_embeddings')), "
                        "(SELECT count(*) FROM document_chunks)"
                    )
                )
            ).one()
    finally:
        await engine.dispose()
    return {"series": series, "explain": explain, "sizes": sizes}


def _report(size: int, load_seconds: float, outcome: dict[str, Any]) -> None:
    series, sizes = outcome["series"], outcome["sizes"]
    print(f"\n## {sizes[3]} chunks (loaded in {load_seconds:.0f} s)")
    print(f"- chunk table {sizes[0]}, search index {sizes[1]}, embeddings {sizes[2]}")
    print("\n| Step | median / p95 ms |\n|---|---|")
    for name in ("dense", "sparse", "fuse", "hydrate", "hybrid_total"):
        print(f"| {name} | {_summary(series[name])} |")
    print("\nLexical query plan (one run):\n```")
    print(outcome["explain"])
    print("```")


def run(sizes: list[int], user_count: int) -> None:
    from testcontainers.postgres import PostgresContainer

    from tests.conftest import POSTGRES_IMAGE

    external = os.environ.get("TEST_DATABASE_URL")
    container = None
    if external:
        base = external
    else:
        container = PostgresContainer(POSTGRES_IMAGE, username="b", password="b", dbname="b")
        container.start()
        base = (
            f"postgresql+asyncpg://b:b@{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(5432)}/b"
        )
    name = f"hybridbench_{uuid.uuid4().hex[:8]}"
    db_autocommit(base, f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    try:
        upgrade(url)
        users = [
            uuid.UUID(create_user_in_db(url, email=f"bench{i}@example.com"))
            for i in range(user_count)
        ]
        config = uuid.uuid4()
        for size in sizes:
            load_seconds = asyncio.run(_populate(url, users, size, config))
            _report(size, load_seconds, asyncio.run(_measure(url, users, config)))
    finally:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if container:
            container.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 100_000])
    parser.add_argument("--users", type=int, default=20)
    args = parser.parse_args()
    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    run(args.sizes, args.users)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
