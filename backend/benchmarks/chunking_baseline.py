"""Baseline timings for chunking and for persisting chunks (batched vs row-by-row inserts).

Run from `backend/` with Docker available (a throwaway PostgreSQL is started with
Testcontainers) or with TEST_DATABASE_URL pointing at a PostgreSQL server:

    python -m benchmarks.chunking_baseline

Numbers depend on the machine; record them with the hardware.
"""

import asyncio
import hashlib
import os
import platform
import statistics
import sys
import time
import tracemalloc
import uuid
from collections.abc import Callable

from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.chunking.chunker import StructureAwareChunker
from app.core.chunking.types import ChunkDraft, ParagraphMode, SectionText
from app.db.repositories.chunk_repository import ChunkRepository, ChunkRow
from app.models.document_chunk import DocumentChunk
from benchmarks.chunking_eval import (
    DEFAULT_CONFIG,
    ENGLISH_SENTENCES,
    MIXED_SENTENCES,
    _NoDeadline,
    _paragraphs,
)
from tests.helpers import create_user_in_db, db_autocommit, db_execute, upgrade

REPEATS = 5


def _time(call: Callable[[], object]) -> float:
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


def cpu_cases() -> list[tuple[str, list[SectionText]]]:
    return [
        (
            "English 0.5M chars (1 section)",
            [SectionText(0, _paragraphs(ENGLISH_SENTENCES, 1600, seed=11))],
        ),
        (
            "Mixed 2.4M chars (1 section)",
            [SectionText(0, _paragraphs(MIXED_SENTENCES, 7500, seed=12))],
        ),
        (
            "Mixed 500 pages",
            [
                SectionText(i, _paragraphs(MIXED_SENTENCES, 5, seed=300 + i), page_number=i + 1)
                for i in range(500)
            ],
        ),
        (
            "DOCX-style 2,000 lines",
            [
                SectionText(
                    0,
                    _paragraphs(MIXED_SENTENCES, 2000, seed=13, max_sentences=4).replace(
                        "\n\n", "\n"
                    ),
                    paragraph_mode=ParagraphMode.LINE,
                )
            ],
        ),
        ("No whitespace 1M chars", [SectionText(0, "x" * 1_000_000)]),
    ]


def measure_cpu(sections: list[SectionText]) -> dict[str, float | int]:
    chunker = StructureAwareChunker(DEFAULT_CONFIG)
    result = chunker.chunk(sections, _NoDeadline())
    chars = sum(len(s.text) for s in sections)
    times = [_time(lambda: chunker.chunk(sections, _NoDeadline())) for _ in range(REPEATS)]
    tracemalloc.start()
    chunker.chunk(sections, _NoDeadline())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    median_ms = statistics.median(times) * 1000
    return {
        "chars": chars,
        "chunks": len(result.chunks),
        "ms": round(median_ms, 1),
        "mchars_per_s": round(chars / (median_ms / 1000) / 1e6, 2),
        "py_peak_mb": round(peak / 1024 / 1024, 1),
    }


def _drafts(count: int) -> list[ChunkDraft]:
    body = ENGLISH_SENTENCES[3] + " " + ENGLISH_SENTENCES[0]
    return [
        ChunkDraft(
            chunk_index=i, section_ordinal=0, text=body, start_char=i * 10,
            end_char=i * 10 + len(body), overlap_chars=0, page_number=1, heading="H",
            heading_level=1, heading_path=("H",),
        )
        for i in range(count)
    ]  # fmt: skip


def _row(document_id: uuid.UUID, section_id: uuid.UUID, draft: ChunkDraft) -> dict[str, object]:
    return {
        "document_id": document_id, "section_id": section_id, "chunking_version": "c1.0",
        "chunk_index": draft.chunk_index, "text": draft.text, "char_count": len(draft.text),
        "text_sha256": hashlib.sha256(draft.text.encode()).hexdigest(),
        "start_char": draft.start_char, "end_char": draft.end_char, "overlap_chars": 0,
        "page_number": 1, "heading": "H", "heading_level": 1, "heading_path": ["H"],
    }  # fmt: skip


async def _insert_timings(
    url: str, document_id: uuid.UUID, section_id: uuid.UUID, count: int
) -> dict[str, float]:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    drafts = _drafts(count)
    timings: dict[str, float] = {}
    try:
        async with sessions() as session:
            rows = [ChunkRow(draft, section_id) for draft in drafts]
            started = time.perf_counter()
            await ChunkRepository(session).replace_all(document_id, "c1.0", rows)
            await session.commit()
            timings["batched_ms"] = (time.perf_counter() - started) * 1000
        async with sessions() as session:
            started = time.perf_counter()
            await _row_by_row(session, document_id, section_id, drafts)
            await session.commit()
            timings["row_by_row_ms"] = (time.perf_counter() - started) * 1000
    finally:
        await engine.dispose()
    return timings


async def _row_by_row(
    session: AsyncSession, document_id: uuid.UUID, section_id: uuid.UUID, drafts: list[ChunkDraft]
) -> None:
    await session.execute(
        text("DELETE FROM document_chunks WHERE document_id = :d"), {"d": document_id}
    )
    for draft in drafts:
        await session.execute(insert(DocumentChunk), _row(document_id, section_id, draft))


def measure_inserts(url: str, counts: tuple[int, ...]) -> list[tuple[int, dict[str, float]]]:
    user = create_user_in_db(url, email=f"bench-{uuid.uuid4().hex[:6]}@example.com")
    results = []
    for count in counts:
        document_id, section_id = uuid.uuid4(), uuid.uuid4()
        db_execute(
            url,
            "INSERT INTO documents (id, user_id, filename, storage_key, file_type, content_type, "
            "file_size, checksum_sha256) VALUES (:i, :u, 'b.txt', :k, 'txt', 'text/plain', 1, :c)",
            i=str(document_id), u=user, k=f"documents/bb/{uuid.uuid4().hex}",
            c=uuid.uuid4().hex * 2,
        )  # fmt: skip
        db_execute(
            url,
            "INSERT INTO document_sections (id, document_id, ordinal, kind, text, char_count) "
            "VALUES (:s, :d, 0, 'section', 'x', 1)",
            s=str(section_id), d=str(document_id),
        )  # fmt: skip
        results.append((count, asyncio.run(_insert_timings(url, document_id, section_id, count))))
    return results


def _database_url() -> tuple[str, Callable[[], None]]:
    from testcontainers.postgres import PostgresContainer

    from tests.conftest import POSTGRES_IMAGE

    external = os.environ.get("TEST_DATABASE_URL")
    container = None
    if external:
        base = external
    else:
        container = PostgresContainer(
            POSTGRES_IMAGE, username="bench", password="bench", dbname="bench"  # noqa: S106
        )
        container.start()
        base = (
            f"postgresql+asyncpg://bench:bench@{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(5432)}/bench"
        )
    name = f"bench_{uuid.uuid4().hex[:8]}"
    db_autocommit(base, f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    upgrade(url)

    def cleanup() -> None:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if container:
            container.stop()

    return url, cleanup


def main() -> None:
    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    print(f"Chunking config {DEFAULT_CONFIG.snapshot()}; median of {REPEATS} runs\n")
    print("| Input | Chars | Chunks | Chunking ms | M chars/s | Py peak MB |")
    print("|---|---|---|---|---|---|")
    for name, sections in cpu_cases():
        r = measure_cpu(sections)
        cells = [name, f"{r['chars']:,}", f"{r['chunks']:,}", r["ms"], r["mchars_per_s"]]
        print("| " + " | ".join(str(cell) for cell in [*cells, r["py_peak_mb"]]) + " |")
    sys.stdout.flush()

    url, cleanup = _database_url()
    try:
        print("\n| Chunks persisted | Batched insert ms | Row-by-row insert ms | Speed-up |")
        print("|---|---|---|---|")
        for count, t in measure_inserts(url, (100, 1_000, 10_000)):
            speedup = t["row_by_row_ms"] / t["batched_ms"]
            print(
                f"| {count:,} | {t['batched_ms']:.0f} | {t['row_by_row_ms']:.0f} | {speedup:.1f}x |"
            )
    finally:
        cleanup()
    sys.stdout.flush()


if __name__ == "__main__":
    main()
