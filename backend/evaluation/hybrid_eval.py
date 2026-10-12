"""Dense vs sparse vs hybrid retrieval ablation on the combined evaluation datasets.

Run from `backend/` (see evaluation/retrieval/README.md):

    python -m evaluation.hybrid_eval --json evaluation/results/hybrid_e5-small_database.json

Both datasets (the base set and the lexical companion) are loaded as documents of one user, embedded
with the real EmbeddingService, and searched through the real HybridSearchService against
PostgreSQL/pgvector (Testcontainers, or TEST_DATABASE_URL). The same service answers three ways:
dense only, sparse only, and both fused with Reciprocal Rank Fusion. A small parameter sweep fuses
the recorded rankings offline; it is reported for transparency, not used to tune the defaults.
Numbers come from the machine that runs this and from a small synthetic dataset.
"""

import argparse
import asyncio
import json
import platform
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.repositories.lexical_repository import LexicalRepository
from app.db.session import create_engine
from app.embeddings.registry import get_provider
from app.models.user import User
from app.retrieval.fusion import overlap, reciprocal_rank_fusion
from app.services.hybrid_search_service import HybridSearchService, Retriever
from evaluation.dataset import Dataset, load_combined
from evaluation.metrics import QueryResult, strip_neutral, summarise
from evaluation.retrieval_eval import (
    _database,
    _percentile,
    _settings_for,
    load_and_embed,
)
from tests.helpers import create_user_in_db

MODES: dict[str, frozenset[Retriever]] = {
    "dense": frozenset({"dense"}),
    "sparse": frozenset({"sparse"}),
    "hybrid": frozenset({"dense", "sparse"}),
}
SWEEP_K = (10, 30, 60, 100)
SWEEP_WEIGHTS = ((1.0, 1.0), (1.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 1.0))


def _group_summary(results: list[QueryResult]) -> dict[str, float]:
    summary = summarise(results)
    return asdict(summary) if summary else {}


def _results(
    dataset: Dataset, rankings: dict[str, list[str]], top_scores: dict[str, float] | None = None
) -> list[QueryResult]:
    results = []
    for query in dataset.queries:
        ranking = tuple(strip_neutral(rankings[query.id], query.neutral))
        results.append(
            QueryResult(
                query.id, query.category, query.relevant, ranking,
                (top_scores or {}).get(query.id, 0.0),
            )
        )  # fmt: skip
    return results


def _breakdowns(
    dataset: Dataset, results: list[QueryResult], lexical_ids: frozenset[str]
) -> dict[str, Any]:
    language_of = {p.id: p.language for p in dataset.passages}
    by_language: dict[str, list[QueryResult]] = defaultdict(list)
    for result in results:
        if result.relevant:
            by_language[language_of[result.relevant[0]]].append(result)
    by_category: dict[str, list[QueryResult]] = defaultdict(list)
    for result in results:
        by_category[result.category].append(result)
    return {
        "overall": _group_summary(results),
        "base_set": _group_summary([r for r in results if r.query_id not in lexical_ids]),
        "lexical_set": _group_summary([r for r in results if r.query_id in lexical_ids]),
        "by_language": {k: _group_summary(v) for k, v in sorted(by_language.items())},
        "by_category": {
            k: _group_summary(v) for k, v in sorted(by_category.items()) if v[0].relevant
        },
    }  # fmt: skip


def _fuse_offline(
    dataset: Dataset,
    dense: dict[str, list[str]],
    sparse: dict[str, list[str]],
    k: int,
    weights: tuple[float, float],
) -> dict[str, list[str]]:
    fused = {}
    for query in dataset.queries:
        rankings = {"dense": dense[query.id], "sparse": sparse[query.id]}
        items = reciprocal_rank_fusion(
            rankings, k=k, weights={"dense": weights[0], "sparse": weights[1]}
        )
        fused[query.id] = [item.key for item in items]
    return fused


async def evaluate(
    settings: Settings, dataset: Dataset, lexical_ids: frozenset[str], user_id: uuid.UUID
) -> dict[str, Any]:
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = get_provider(settings)
    provider.warmup()
    try:
        chunk_to_passage, embed_seconds, failures = await load_and_embed(
            settings, provider, factory, dataset, user_id
        )
        rankings: dict[str, dict[str, list[str]]] = {m: {} for m in MODES}
        latencies: dict[str, list[float]] = {m: [] for m in MODES}
        timings: dict[str, dict[str, list[float]]] = {"dense": defaultdict(list)}
        overlaps: list[int] = []
        candidates: dict[str, list[tuple[int, int]]] = {"dense": [], "sparse": []}
        async with factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            service = HybridSearchService(
                session=session, documents=DocumentRepository(session),
                collections=CollectionRepository(session), embeddings=EmbeddingRepository(session),
                lexical=LexicalRepository(session), provider=provider,
                gate=asyncio.Semaphore(1), settings=settings,
            )  # fmt: skip
            for query in dataset.queries:
                for mode, retrievers in MODES.items():
                    await session.refresh(user)  # the service rolls back, which expires it
                    started = time.perf_counter()
                    outcome = await service.search(
                        user, query=query.query, top_k=settings.search_max_top_k,
                        document_ids=None, collection_id=None, retrievers=retrievers,
                    )  # fmt: skip
                    latencies[mode].append((time.perf_counter() - started) * 1000)
                    rankings[mode][query.id] = [
                        chunk_to_passage[r.record.chunk_id] for r in outcome.results
                    ]
                    if mode == "hybrid":
                        overlaps.append(outcome.diagnostics.overlap)
                        timings["dense"]["embed_ms"].append(outcome.diagnostics.embed_ms)
                        timings["dense"]["dense_ms"].append(outcome.diagnostics.dense_ms)
                        timings["dense"]["sparse_ms"].append(outcome.diagnostics.sparse_ms)
                        timings["dense"]["fuse_ms"].append(outcome.diagnostics.fuse_ms)
                        candidates["dense"].append((outcome.diagnostics.dense_candidates, 0))
                        candidates["sparse"].append((outcome.diagnostics.sparse_candidates, 0))

        report: dict[str, Any] = {
            "model": provider.config.model,
            "revision": provider.config.revision,
            "dimension": provider.config.dimension,
            "device": str(getattr(provider, "device", "n/a")),
            "passages": len(dataset.passages),
            "queries": len(dataset.queries),
            "lexical_queries": len(lexical_ids),
            "unanswerable": sum(1 for q in dataset.queries if not q.relevant),
            "embed_seconds": round(embed_seconds, 2),
            "embedding_failures": failures,
            "sparse_remove_stopwords": settings.sparse_remove_stopwords,
            "fusion": {
                "method": "rrf",
                "k": settings.hybrid_rrf_k,
                "weights": [settings.hybrid_dense_weight, settings.hybrid_sparse_weight],
                "candidates": [settings.hybrid_dense_candidates, settings.hybrid_sparse_candidates],
            },
            "modes": {},
        }
        for mode in MODES:
            results = _results(dataset, rankings[mode])
            entry = _breakdowns(dataset, results, lexical_ids)
            entry["latency_ms"] = {
                "median": round(statistics.median(latencies[mode]), 1),
                "p95": round(_percentile(latencies[mode], 0.95), 1),
            }
            report["modes"][mode] = entry
        report["hybrid_stage_ms_median"] = {
            name: round(statistics.median(values), 1) for name, values in timings["dense"].items()
        }
        report["mean_dense_sparse_overlap"] = round(statistics.fmean(overlaps), 2)
        report["queries_with_no_lexical_candidates"] = sum(
            1 for q in dataset.queries if not rankings["sparse"][q.id]
        )
        report["comparison"] = _compare(dataset, rankings)
        report["sweep"] = _sweep(dataset, rankings, lexical_ids)
        report["failures"] = _failures(dataset, rankings)
        return report
    finally:
        await engine.dispose()


def _top1_hit(dataset: Dataset, ranking: dict[str, list[str]], query_id: str) -> bool:
    query = next(q for q in dataset.queries if q.id == query_id)
    kept = strip_neutral(ranking[query_id], query.neutral)
    return bool(query.relevant) and bool(kept) and kept[0] in query.relevant


def _compare(dataset: Dataset, rankings: dict[str, dict[str, list[str]]]) -> dict[str, Any]:
    answerable = [q.id for q in dataset.queries if q.relevant]
    hybrid_only = [q for q in answerable if _top1_hit(dataset, rankings["hybrid"], q)]
    return {
        "top1_hits": {
            m: sum(_top1_hit(dataset, rankings[m], q) for q in answerable) for m in MODES
        },
        "hybrid_top1_but_dense_not": [
            q for q in hybrid_only if not _top1_hit(dataset, rankings["dense"], q)
        ],
        "dense_top1_but_hybrid_not": [
            q for q in answerable
            if _top1_hit(dataset, rankings["dense"], q)
            and not _top1_hit(dataset, rankings["hybrid"], q)
        ],
        "hybrid_top1_but_sparse_not": [
            q for q in hybrid_only if not _top1_hit(dataset, rankings["sparse"], q)
        ],
        "sparse_top1_but_hybrid_not": [
            q for q in answerable
            if _top1_hit(dataset, rankings["sparse"], q)
            and not _top1_hit(dataset, rankings["hybrid"], q)
        ],
        "dense_candidate_overlap_with_sparse_top10": round(
            statistics.fmean(
                overlap(rankings["dense"][q][:10], rankings["sparse"][q][:10]) for q in answerable
            ),
            2,
        ),
    }  # fmt: skip


def _sweep(
    dataset: Dataset, rankings: dict[str, dict[str, list[str]]], lexical_ids: frozenset[str]
) -> list[dict[str, Any]]:
    rows = []
    for k in SWEEP_K:
        for weights in SWEEP_WEIGHTS:
            fused = _fuse_offline(dataset, rankings["dense"], rankings["sparse"], k, weights)
            results = _results(dataset, fused)
            overall = summarise(results)
            base = summarise([r for r in results if r.query_id not in lexical_ids])
            lexical = summarise([r for r in results if r.query_id in lexical_ids])
            assert overall and base and lexical
            rows.append(
                {
                    "k": k, "dense_weight": weights[0], "sparse_weight": weights[1],
                    "recall_at_1": round(overall.recall_at_1, 3),
                    "mrr_at_10": round(overall.mrr_at_10, 3),
                    "ndcg_at_10": round(overall.ndcg_at_10, 3),
                    "base_mrr": round(base.mrr_at_10, 3),
                    "lexical_mrr": round(lexical.mrr_at_10, 3),
                }
            )  # fmt: skip
    return rows


def _failures(dataset: Dataset, rankings: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    failures = []
    for query in dataset.queries:
        if not query.relevant or _top1_hit(dataset, rankings["hybrid"], query.id):
            continue
        kept = strip_neutral(rankings["hybrid"][query.id], query.neutral)
        rank = next((i for i, p in enumerate(kept, 1) if p in query.relevant), None)
        failures.append(
            {
                "query": query.id, "category": query.category, "text": query.query,
                "top1": kept[0] if kept else None, "rank_of_relevant": rank,
            }
        )  # fmt: skip
    return failures


def print_report(report: dict[str, Any]) -> None:
    print(
        f"model={report['model']} revision={report['revision'][:12]} dim={report['dimension']} "
        f"device={report['device']}"
    )
    print(
        f"{report['passages']} passages, {report['queries']} queries "
        f"({report['lexical_queries']} lexical set, {report['unanswerable']} unanswerable)"
    )
    print(f"fusion: {report['fusion']}")
    print()
    for set_name in ("overall", "base_set", "lexical_set"):
        print(f"### {set_name}")
        print("| Mode | Queries | Recall@1 | Recall@3 | Recall@10 | MRR@10 | nDCG@10 |")
        print("|---|---|---|---|---|---|---|")
        for mode, entry in report["modes"].items():
            s = entry[set_name]
            print(
                f"| {mode} | {int(s['queries'])} | {s['recall_at_1']:.3f} | "
                f"{s['recall_at_3']:.3f} | "
                f"{s['recall_at_10']:.3f} | {s['mrr_at_10']:.3f} | {s['ndcg_at_10']:.3f} |"
            )
        print()
    print("### by category (MRR@10 / Recall@1)")
    categories = sorted(report["modes"]["dense"]["by_category"])
    print("| Category | Queries | dense | sparse | hybrid |")
    print("|---|---|---|---|---|")
    for category in categories:
        cells = []
        for mode in MODES:
            s = report["modes"][mode]["by_category"][category]
            cells.append(f"{s['mrr_at_10']:.3f} / {s['recall_at_1']:.3f}")
        count = int(report["modes"]["dense"]["by_category"][category]["queries"])
        print(f"| {category} | {count} | " + " | ".join(cells) + " |")
    print()
    print("### by document language (MRR@10)")
    for language in sorted(report["modes"]["dense"]["by_language"]):
        cells = [
            f"{mode} {report['modes'][mode]['by_language'][language]['mrr_at_10']:.3f}"
            for mode in MODES
        ]
        queries = int(report["modes"]["dense"]["by_language"][language]["queries"])
        print(f"- {language} ({queries}): " + ", ".join(cells))
    print()
    print("### latency in process, ms (median / p95)")
    for mode, entry in report["modes"].items():
        print(f"- {mode}: {entry['latency_ms']['median']} / {entry['latency_ms']['p95']}")
    print(f"- hybrid stage medians: {report['hybrid_stage_ms_median']}")
    print(f"- mean dense/sparse overlap: {report['mean_dense_sparse_overlap']}")
    print(f"- {report['comparison']}")
    print()
    print("### fusion parameter sweep (overall)")
    print("| k | dense w | sparse w | Recall@1 | MRR@10 | nDCG@10 | base MRR | lexical MRR |")
    print("|---|---|---|---|---|---|---|---|")
    for row in report["sweep"]:
        print(
            f"| {row['k']} | {row['dense_weight']} | {row['sparse_weight']} | "
            f"{row['recall_at_1']} | "
            f"{row['mrr_at_10']} | {row['ndcg_at_10']} | {row['base_mrr']} | {row['lexical_mrr']} |"
        )
    print()
    print("Queries whose hybrid top result was not relevant:")
    for f in report["failures"]:
        print(
            f"- {f['query']} ({f['category']}) {f['text']!r}: "
            f"top1={f['top1']}, rank {f['rank_of_relevant']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--no-stopword-filter", action="store_true", help="keep function words in the lexical query"
    )
    args = parser.parse_args()

    import torch

    print(
        f"Python {platform.python_version()} on {platform.platform()} "
        f"({platform.processor()}), torch {torch.__version__}, {torch.get_num_threads()} threads"
    )
    dataset, lexical_ids = load_combined()
    url, cleanup = _database()
    try:
        owner = uuid.UUID(create_user_in_db(url, email="eval@example.com"))
        settings = _settings_for(url, None).model_copy(
            update={"sparse_remove_stopwords": not args.no_stopword_filter}
        )
        report = asyncio.run(evaluate(settings, dataset, lexical_ids, owner))
    finally:
        cleanup()
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
