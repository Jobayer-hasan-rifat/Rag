"""Retrieval evaluation of the configured embedding model on the synthetic dataset.

Run from `backend/` (see evaluation/retrieval/README.md):

    python -m evaluation.retrieval_eval --mode memory               # model only, fast
    python -m evaluation.retrieval_eval --mode database             # the real pipeline end to end
    python -m evaluation.retrieval_eval --mode database --json out.json

`memory` embeds passages and queries with the application's provider and ranks by cosine
similarity in numpy. `database` loads the passages as documents, runs the real EmbeddingService
and SearchService against PostgreSQL/pgvector (Testcontainers, or TEST_DATABASE_URL) and ranks by
the actual search endpoint's logic. Both use the configured model (EMBEDDING_* settings or the
defaults); the first run downloads it. Numbers are measured on the machine that runs it.
"""

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.session import create_engine
from app.embeddings.registry import embedding_config, get_provider
from app.embeddings.types import EmbeddingProvider
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_section import DocumentSection
from app.services.embedding_service import EmbeddingService
from app.services.search_service import SearchService
from evaluation.dataset import Dataset, load_dataset
from evaluation.metrics import QueryResult, answerability_auroc, strip_neutral, summarise
from tests.helpers import create_user_in_db, db_autocommit, upgrade


@dataclass
class EvalReport:
    mode: str
    model: str
    revision: str
    dimension: int
    device: str
    passages: int
    queries: int
    unanswerable: int
    embed_seconds: float
    passages_per_second: float
    embedding_failures: int
    truncated_passages: int
    query_ms_median: float
    query_ms_p95: float
    overall: dict[str, float] = field(default_factory=dict)
    answerability_auroc: float | None = None
    top_score_answerable: float | None = None
    top_score_unanswerable: float | None = None
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _summarise(results: list[QueryResult], report: EvalReport, dataset: Dataset) -> None:
    overall = summarise(results)
    report.overall = asdict(overall) if overall else {}
    report.answerability_auroc = answerability_auroc(results)
    answerable = [r.top_score for r in results if r.relevant]
    unanswerable = [r.top_score for r in results if not r.relevant]
    report.top_score_answerable = statistics.fmean(answerable) if answerable else None
    report.top_score_unanswerable = statistics.fmean(unanswerable) if unanswerable else None
    for category in sorted({r.category for r in results}):
        summary = summarise([r for r in results if r.category == category])
        if summary:
            report.by_category[category] = asdict(summary)
    for result in results:
        if result.relevant and result.ranking and result.ranking[0] not in result.relevant:
            query = next(q for q in dataset.queries if q.id == result.query_id)
            rank = next((i for i, p in enumerate(result.ranking, 1) if p in result.relevant), None)
            report.failures.append(
                {"query": query.id, "category": query.category, "relevant": list(result.relevant),
                 "top1": result.ranking[0], "rank_of_relevant": rank}
            )  # fmt: skip


# --- memory mode ---------------------------------------------------------------------------


def evaluate_memory(provider: EmbeddingProvider, dataset: Dataset, batch_size: int) -> EvalReport:
    provider.warmup()
    passages = dataset.passages
    inputs = [
        f"{p.heading}\n{p.text}" if provider.config.include_heading else p.text for p in passages
    ]
    provider.embed_documents(inputs[:2])  # warm-up, excluded from timings

    started = time.perf_counter()
    vectors: list[np.ndarray] = []
    truncated = 0
    for start in range(0, len(inputs), batch_size):
        batch = provider.embed_documents(inputs[start : start + batch_size])
        vectors.append(batch.vectors)
        truncated += batch.truncated_count
    embed_seconds = time.perf_counter() - started
    matrix = np.vstack(vectors)

    ids = [p.id for p in passages]
    results: list[QueryResult] = []
    latencies: list[float] = []
    for query in dataset.queries:
        t0 = time.perf_counter()
        vector = provider.embed_query(query.query)
        scores = matrix @ vector
        latencies.append((time.perf_counter() - t0) * 1000)
        order = np.argsort(-scores, kind="stable")
        ranking = strip_neutral([ids[i] for i in order], query.neutral)
        results.append(
            QueryResult(
                query.id, query.category, query.relevant, tuple(ranking), float(scores[order[0]])
            )
        )
    report = _base_report("memory", provider, dataset, embed_seconds, 0, truncated, latencies)
    _summarise(results, report, dataset)
    return report


def _base_report(
    mode: str,
    provider: EmbeddingProvider,
    dataset: Dataset,
    embed_seconds: float,
    failures: int,
    truncated: int,
    latencies: list[float],
) -> EvalReport:
    config = provider.config
    return EvalReport(
        mode=mode,
        model=config.model,
        revision=config.revision,
        dimension=config.dimension,
        device=str(getattr(provider, "device", "n/a")),
        passages=len(dataset.passages),
        queries=len(dataset.queries),
        unanswerable=sum(1 for q in dataset.queries if not q.relevant),
        embed_seconds=round(embed_seconds, 2),
        passages_per_second=(
            round(len(dataset.passages) / embed_seconds, 1) if embed_seconds else 0.0
        ),
        embedding_failures=failures,
        truncated_passages=truncated,
        query_ms_median=round(statistics.median(latencies), 1),
        query_ms_p95=round(_percentile(latencies, 0.95), 1),
    )


# --- database mode -------------------------------------------------------------------------


async def evaluate_database(settings: Settings, dataset: Dataset, user_id: uuid.UUID) -> EvalReport:
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = get_provider(settings)
    provider.warmup()
    try:
        chunk_to_passage: dict[uuid.UUID, str] = {}
        document_ids: list[uuid.UUID] = []
        async with factory() as session:
            from app.models.user import User

            user = await session.get(User, user_id)
            assert user is not None
            for document in dataset.documents:
                doc_id = uuid.uuid4()
                document_ids.append(doc_id)
                session.add(
                    Document(
                        id=doc_id, user_id=user_id, filename=f"{document.id}.md",
                        storage_key=f"documents/ev/{uuid.uuid4().hex}", file_type="md",
                        content_type="text/markdown", file_size=1,
                        checksum_sha256=uuid.uuid4().hex * 2, status="chunked",
                        chunk_count=len(document.passages), chunking_version="eval",
                    )
                )  # fmt: skip
                await session.flush()
                for ordinal, passage in enumerate(document.passages):
                    section_id, chunk_id = uuid.uuid4(), uuid.uuid4()
                    session.add(
                        DocumentSection(
                            id=section_id, document_id=doc_id, ordinal=ordinal, kind="section",
                            heading=passage.heading, heading_level=2, text=passage.text,
                            char_count=len(passage.text),
                        )
                    )  # fmt: skip
                    await session.flush()
                    import hashlib

                    session.add(
                        DocumentChunk(
                            id=chunk_id, document_id=doc_id, section_id=section_id,
                            chunking_version="eval", chunk_index=ordinal, text=passage.text,
                            char_count=len(passage.text),
                            text_sha256=hashlib.sha256(passage.text.encode()).hexdigest(),
                            start_char=0, end_char=len(passage.text), overlap_chars=0,
                            heading=passage.heading, heading_level=2,
                            heading_path=[document.title, passage.heading],
                        )
                    )  # fmt: skip
                    chunk_to_passage[chunk_id] = passage.id
            await session.commit()

        embed_started = time.perf_counter()
        failures = 0
        for doc_id in document_ids:
            async with factory() as session:
                service = EmbeddingService(
                    session=session, documents=DocumentRepository(session),
                    embeddings=EmbeddingRepository(session), provider=provider, settings=settings,
                )  # fmt: skip
                outcome = await service.embed(doc_id, task_id="eval")
                failures += outcome.status != "ready"
        embed_seconds = time.perf_counter() - embed_started

        results: list[QueryResult] = []
        latencies: list[float] = []
        gate = asyncio.Semaphore(1)
        async with factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            search = SearchService(
                session=session, documents=DocumentRepository(session),
                collections=CollectionRepository(session), embeddings=EmbeddingRepository(session),
                provider=provider, gate=gate, settings=settings,
            )  # fmt: skip
            for query in dataset.queries:
                t0 = time.perf_counter()
                found = await search.semantic(
                    user, query=query.query, top_k=settings.search_max_top_k,
                    document_ids=None, collection_id=None, min_score=None,
                )  # fmt: skip
                latencies.append((time.perf_counter() - t0) * 1000)
                ranked = [chunk_to_passage[r.hit.chunk_id] for r in found.results]
                top_score = found.results[0].score if found.results else -1.0
                results.append(
                    QueryResult(
                        query.id, query.category, query.relevant,
                        tuple(strip_neutral(ranked, query.neutral)), top_score,
                    )
                )  # fmt: skip
        async with factory() as session:
            truncated = int(
                (
                    await session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT count(*) FROM chunk_embeddings WHERE truncated"
                        )
                    )
                ).scalar_one()
            )
        report = _base_report(
            "database", provider, dataset, embed_seconds, failures, truncated, latencies
        )
        _summarise(results, report, dataset)
        return report
    finally:
        await engine.dispose()


def _settings_for(url: str, base: Settings | None) -> Settings:
    values: dict[str, Any] = {
        "database_url": url,
        "redis_url": "redis://127.0.0.1:6379/0",
        "celery_broker_url": "redis://127.0.0.1:6379/1",
        "celery_result_backend": "redis://127.0.0.1:6379/2",
        "jwt_secret_key": "evaluation-only-secret-key-0123456789",
        "search_max_top_k": 50,
        "embedding_task_budget_seconds": 3000,
        "embedding_stale_after_seconds": 7200,
    }
    return Settings(_env_file=None, **values)


def _database() -> tuple[str, Any]:
    from testcontainers.postgres import PostgresContainer

    from tests.conftest import POSTGRES_IMAGE

    external = os.environ.get("TEST_DATABASE_URL")
    container = None
    if external:
        base = external
    else:
        container = PostgresContainer(
            POSTGRES_IMAGE, username="eval", password="eval", dbname="eval"
        )
        container.start()
        base = (
            f"postgresql+asyncpg://eval:eval@{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(5432)}/eval"
        )
    name = f"eval_{uuid.uuid4().hex[:8]}"
    db_autocommit(base, f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    upgrade(url)

    def cleanup() -> None:
        db_autocommit(base, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if container:
            container.stop()

    return url, cleanup


# --- reporting -----------------------------------------------------------------------------


def print_report(report: EvalReport) -> None:
    o = report.overall
    print(
        f"mode={report.mode} model={report.model} revision={report.revision[:12]} "
        f"dim={report.dimension} device={report.device}"
    )
    print(
        f"{report.passages} passages, {report.queries} queries "
        f"({report.unanswerable} unanswerable)"
    )
    print()
    print("| Metric | Value |")
    print("|---|---|")
    for name, key in (
        ("Recall@1", "recall_at_1"),
        ("Recall@3", "recall_at_3"),
        ("Recall@5", "recall_at_5"),
        ("Recall@10", "recall_at_10"),
        ("MRR@10", "mrr_at_10"),
        ("nDCG@10", "ndcg_at_10"),
    ):
        print(f"| {name} | {o[key]:.3f} |")
    answerable, unanswerable = report.top_score_answerable, report.top_score_unanswerable
    print(f"| Answerability AUROC (top score) | {report.answerability_auroc:.3f} |")
    print(f"| Mean top score, answerable / unanswerable | {answerable:.3f} / {unanswerable:.3f} |")
    print(
        f"| Embedding throughput | {report.passages_per_second} passages/s "
        f"({report.embed_seconds} s) |"
    )
    print(f"| Query latency median / p95 | {report.query_ms_median} / {report.query_ms_p95} ms |")
    print(f"| Embedding failures | {report.embedding_failures} documents |")
    print(f"| Truncated passages | {report.truncated_passages} |")
    header = "| Category | Queries | Recall@1 | Recall@3 | MRR@10 | nDCG@10 |"
    print()
    print(header)
    print("|---|---|---|---|---|---|")
    for category, s in report.by_category.items():
        print(
            f"| {category} | {int(s['queries'])} | {s['recall_at_1']:.3f} | "
            f"{s['recall_at_3']:.3f} | {s['mrr_at_10']:.3f} | {s['ndcg_at_10']:.3f} |"
        )
    if report.failures:
        print()
        print("Queries whose top result was not relevant:")
        for f in report.failures:
            print(
                f"- {f['query']} ({f['category']}): top1={f['top1']}, "
                f"relevant {f['relevant']} at rank {f['rank_of_relevant']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=["memory", "database"], default="memory")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    import torch

    print(
        f"Python {platform.python_version()} on {platform.platform()} "
        f"({platform.processor()}), torch {torch.__version__}, "
        f"{torch.get_num_threads()} threads, cuda={torch.cuda.is_available()}"
    )
    dataset = load_dataset()
    if args.mode == "memory":
        base = Settings(
            _env_file=None, database_url="postgresql+asyncpg://x:x@localhost/x",
            redis_url="redis://localhost", celery_broker_url="redis://localhost",
            celery_result_backend="redis://localhost", jwt_secret_key="x" * 40,
        )  # fmt: skip
        provider = get_provider(base)
        assert embedding_config(base).fingerprint() == provider.config.fingerprint()
        report = evaluate_memory(provider, dataset, args.batch_size)
    else:
        url, cleanup = _database()
        try:
            owner = uuid.UUID(create_user_in_db(url, email="eval@example.com"))
            report = asyncio.run(evaluate_database(_settings_for(url, None), dataset, owner))
        finally:
            cleanup()
    print_report(report)
    if args.json:
        args.json.write_text(
            json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
