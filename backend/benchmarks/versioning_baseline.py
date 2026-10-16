# ruff: noqa: E501  (a report script: long print strings read better unwrapped)
"""Cost of document versions in the listing, version and retrieval queries.

Run from `backend/` with Docker available (a throwaway PostgreSQL through Testcontainers):

    PYTHONPATH=. python benchmarks/versioning_baseline.py [--families 1500]

Seeds several users with documents of one to three versions (ready, one active per document, a
few chunks and a real 384-dimension vector per chunk) and measures, through the repositories the
application uses:
- document listing and version listing latency and database statements;
- the dense candidate query with the versioned eligibility rule against the same query without it
  (the pre-versioning rule: every ready document, which would also return superseded text);
- the lexical candidate query with and without the rule;
- the query plans, to confirm the partial indexes are used.
Numbers are measured on the machine that runs this, on the dataset size printed first.
"""

import argparse
import asyncio
import os
import platform
import statistics
import time
import uuid
from typing import Any

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://bench:bench@127.0.0.1:1/bench")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:1/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://127.0.0.1:1/2")
os.environ.setdefault("JWT_SECRET_KEY", "benchmark-only-secret-key-0123456789")

import numpy as np
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.repositories.document_repository import DocumentFilters, DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.repositories.lexical_repository import _SQL, LexicalRepository
from app.retrieval.lexical_query import parse_lexical_query
from tests.conftest import POSTGRES_IMAGE
from tests.helpers import create_user_in_db, db_autocommit, upgrade

USERS = 5
CHUNKS_PER_VERSION = 8
DIM = 384
RUNS = 40
VOCABULARY = [f"word{n}" for n in range(400)]


def pct(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def report(label: str, times: list[float], statements: int | None = None) -> None:
    extra = "" if statements is None else f" | {statements}"
    print(f"| {label} | {statistics.median(times):.1f} / {pct(times, 0.95):.1f}{extra} |")


class Counter:
    def __init__(self, engine: Any) -> None:
        self.count = 0
        event.listen(engine.sync_engine, "before_cursor_execute", self._on)

    def _on(self, *args: Any) -> None:
        self.count += 1


async def seed(
    engine: Any, users: list[str], families_per_user: int, rng: np.random.Generator
) -> dict[str, Any]:
    config = uuid.uuid4()
    totals: dict[str, Any] = {"families": 0, "versions": 0, "chunks": 0}
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO embedding_configs (id, fingerprint, provider, model, revision, dimension, "
                "metric, normalized, max_seq_length, include_heading, preprocessing_version) "
                "VALUES (:i, :f, 'p', 'm', 'r', 384, 'cosine', true, 512, false, 'e1')"
            ),
            {"i": config, "f": uuid.uuid4().hex * 2},
        )
        families, docs, sections, chunks, vectors = [], [], [], [], []
        for user in users:
            for _ in range(families_per_user):
                family = uuid.uuid4()
                families.append({"i": family, "u": user})
                versions = int(rng.choice([1, 2, 3], p=[0.4, 0.3, 0.3]))
                for number in range(1, versions + 1):
                    version = family if number == 1 else uuid.uuid4()
                    latest = number == versions
                    docs.append(
                        {
                            "i": version,
                            "u": user,
                            "f": family,
                            "n": number,
                            "a": latest,
                            "c": latest,
                            "k": f"documents/aa/{uuid.uuid4().hex}",
                            "h": uuid.uuid4().hex * 2,
                            "cfg": config,
                            "cc": CHUNKS_PER_VERSION,
                        }
                    )
                    section = uuid.uuid4()
                    sections.append({"s": section, "d": version})
                    for index in range(CHUNKS_PER_VERSION):
                        chunk = uuid.uuid4()
                        words = " ".join(rng.choice(VOCABULARY, size=30))
                        body = f"{words} token{uuid.uuid4().hex[:6]}"
                        chunks.append(
                            {
                                "i": chunk,
                                "d": version,
                                "s": section,
                                "x": index,
                                "t": body,
                                "n": len(body),
                                "h": uuid.uuid4().hex * 2,
                            }
                        )
                        vec = rng.normal(size=DIM).astype(np.float32)
                        vec /= np.linalg.norm(vec)
                        vectors.append(
                            {
                                "c": chunk,
                                "d": version,
                                "u": user,
                                "cfg": config,
                                "e": "[" + ",".join(f"{x:.5f}" for x in vec) + "]",
                                "h": uuid.uuid4().hex * 2,
                            }
                        )
                    totals["versions"] += 1
                    totals["chunks"] += CHUNKS_PER_VERSION
                totals["families"] += 1
        await conn.execute(
            text(
                "INSERT INTO document_families (id, user_id, next_version_number) VALUES (:i, :u, 4)"
            ),
            families,
        )
        await conn.execute(
            text(
                "INSERT INTO documents (id, user_id, family_id, version_number, is_active, is_current, filename, storage_key, "
                "file_type, content_type, file_size, checksum_sha256, status, chunk_count, embedding_config_id, embedded_chunk_count) "
                "VALUES (:i, :u, :f, :n, :a, :c, 'd.txt', :k, 'txt', 'text/plain', 10, :h, 'ready', :cc, :cfg, :cc)"
            ),
            docs,
        )
        await conn.execute(
            text(
                "INSERT INTO document_sections (id, document_id, ordinal, kind, text, char_count) VALUES (:s, :d, 0, 'body', 'x', 1)"
            ),
            sections,
        )
        await conn.execute(
            text(
                "INSERT INTO document_chunks (id, document_id, section_id, chunking_version, chunk_index, text, char_count, text_sha256, start_char, end_char) "
                "VALUES (:i, :d, :s, 'c1.0', :x, :t, :n, :h, 0, :n)"
            ),
            chunks,
        )
        await conn.execute(
            text(
                "INSERT INTO chunk_embeddings (chunk_id, document_id, user_id, config_id, embedding, text_sha256) "
                "VALUES (:c, :d, :u, :cfg, CAST(:e AS vector), :h)"
            ),
            vectors,
        )
        await conn.execute(text("ANALYZE"))
    return {**totals, "config_id": str(config)}


async def measure(url: str, users: list[str], families_per_user: int) -> None:
    rng = np.random.default_rng(7)
    engine = create_async_engine(url)
    counter = Counter(engine)
    try:
        totals = await seed(engine, users, families_per_user, rng)
        config = uuid.UUID(str(totals["config_id"]))
        owner = uuid.UUID(users[0])
        factory = async_sessionmaker(engine, expire_on_commit=False)
        print(
            f"\nDataset: {USERS} users, {totals['families']} documents, {totals['versions']} versions, {totals['chunks']} chunks and vectors ({DIM} dimensions)\n"
        )

        print("## Listing\n\n| Call | median / p95 ms | statements |\n|---|---|---|")
        for label, page in (
            ("list documents, page 1 (20)", 1),
            ("list documents, page 15 (20)", 15),
        ):
            times = []
            for _ in range(RUNS):
                async with factory() as session:
                    repo = DocumentRepository(session)
                    before, started = counter.count, time.perf_counter()
                    items, _ = await repo.list_page(
                        owner,
                        filters=DocumentFilters(),
                        sort="created_at",
                        order="desc",
                        page=page,
                        page_size=20,
                    )
                    await repo.version_summaries([i.family_id for i in items])
                    await repo.collections_for([i.family_id for i in items])
                    times.append((time.perf_counter() - started) * 1000)
                    statements = counter.count - before
            report(label, times, statements)
        async with factory() as session:
            family = (
                await session.execute(
                    text(
                        "SELECT family_id FROM documents WHERE user_id = :u AND version_number = 3 LIMIT 1"
                    ),
                    {"u": owner},
                )
            ).scalar_one()
        times = []
        for _ in range(RUNS):
            async with factory() as session:
                before, started = counter.count, time.perf_counter()
                await DocumentRepository(session).versions_page(family, owner, page=1, page_size=20)
                times.append((time.perf_counter() - started) * 1000)
                statements = counter.count - before
        report("list versions of a 3-version document", times, statements)

        print(
            "\n## Retrieval eligibility (dense candidates, top 50; lexical candidates, top 50)\n\n| Query | median / p95 ms |\n|---|---|"
        )
        queries = []
        for _ in range(RUNS):
            q = rng.normal(size=DIM).astype(np.float32)
            queries.append((q / np.linalg.norm(q)).tolist())

        async def dense(label: str) -> list[float]:
            times = []
            for vector in queries:
                async with factory() as session:
                    started = time.perf_counter()
                    await EmbeddingRepository(session).nearest_candidates(
                        user_id=owner, config_id=config, vector=vector, limit=50
                    )
                    times.append((time.perf_counter() - started) * 1000)
            report(label, times)
            return times

        await dense("dense, versioned rule (ready and active)")
        # the same query with the pre-versioning rule: every ready document (superseded versions included)
        old = []
        for vector in queries:
            async with factory() as session:
                started = time.perf_counter()
                await session.execute(
                    text(
                        "SELECT chunk_id, document_id, embedding <=> CAST(:v AS vector) AS d FROM chunk_embeddings "
                        "WHERE user_id = :u AND config_id = :c AND document_id IN "
                        "(SELECT id FROM documents WHERE user_id = :u AND status = 'ready') ORDER BY d LIMIT 50"
                    ),
                    {
                        "v": "[" + ",".join(f"{x:.5f}" for x in vector) + "]",
                        "u": owner,
                        "c": config,
                    },
                )
                old.append((time.perf_counter() - started) * 1000)
        report("dense, pre-versioning rule (all ready versions)", old)
        filtered = []
        async with factory() as session:
            ids = [
                r[0]
                for r in (
                    await session.execute(
                        text(
                            "SELECT family_id FROM documents WHERE user_id = :u AND is_active LIMIT 10"
                        ),
                        {"u": owner},
                    )
                ).all()
            ]
        for vector in queries:
            async with factory() as session:
                started = time.perf_counter()
                await EmbeddingRepository(session).nearest_candidates(
                    user_id=owner, config_id=config, vector=vector, limit=50, document_ids=ids
                )
                filtered.append((time.perf_counter() - started) * 1000)
        report("dense, versioned rule, filtered to 10 documents", filtered)

        parsed = parse_lexical_query("word12 word77 word301")
        for label, sql in (
            ("lexical, versioned rule", _SQL),
            ("lexical, pre-versioning rule", _SQL.replace(" AND is_active", "")),
        ):
            times = []
            for _ in range(RUNS):
                async with factory() as session:
                    started = time.perf_counter()
                    if sql is _SQL:
                        await LexicalRepository(session).candidates(
                            user_id=owner, query=parsed, limit=50, max_terms=12
                        )
                    else:
                        from sqlalchemy import ARRAY, Text, bindparam

                        stmt = text(sql.format(doc_filter="", collection_filter="")).bindparams(
                            bindparam("phrases", type_=ARRAY(Text)),
                            bindparam("stopwords", type_=ARRAY(Text)),
                        )
                        await session.execute(
                            stmt,
                            {
                                "u": owner,
                                "words": parsed.words,
                                "phrases": list(parsed.phrases),
                                "stopwords": [],
                                "max_terms": 12,
                                "scan_terms": 36,
                                "limit": 50,
                            },
                        )
                    times.append((time.perf_counter() - started) * 1000)
            report(label, times)

        print("\n## Plans\n")
        async with factory() as session:
            plan = (
                await session.execute(
                    text(
                        "EXPLAIN SELECT id FROM documents WHERE user_id = :u AND status = 'ready' AND is_active"
                    ),
                    {"u": owner},
                )
            ).all()
            print(
                "- active ready versions of a user: " + ("; ".join(r[0].strip() for r in plan[:2]))
            )
            plan = (
                await session.execute(
                    text(
                        "EXPLAIN SELECT * FROM documents WHERE user_id = :u AND is_current ORDER BY created_at DESC, id DESC LIMIT 20"
                    ),
                    {"u": owner},
                )
            ).all()
            print("- document listing: " + ("; ".join(r[0].strip() for r in plan[:3])))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--families", type=int, default=1500, help="documents in total (split over the users)"
    )
    args = parser.parse_args()
    from testcontainers.postgres import PostgresContainer

    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    pg = PostgresContainer(POSTGRES_IMAGE, username="b", password="b", dbname="b")
    pg.start()
    try:
        base = (
            f"postgresql+asyncpg://b:b@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/b"
        )
        name = f"verbench_{uuid.uuid4().hex[:8]}"
        db_autocommit(base, f'CREATE DATABASE "{name}"')
        url = make_url(base).set(database=name).render_as_string(hide_password=False)
        upgrade(url)
        users = [create_user_in_db(url, email=f"u{n}@example.com") for n in range(USERS)]
        asyncio.run(measure(url, users, max(1, args.families // USERS)))
    finally:
        pg.stop()


if __name__ == "__main__":
    main()
