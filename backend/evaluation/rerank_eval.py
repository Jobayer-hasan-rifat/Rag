"""Reranking ablation: dense, hybrid, dense + reranker, hybrid + reranker.

Run from `backend/` (Docker is needed for the throwaway PostgreSQL; the models are downloaded
into the Hugging Face cache on first use, see `python -m app.reranking.download`):

    python -m evaluation.rerank_eval --dataset dev --models mmarco bge --candidates 10 30 \
        --json evaluation/results/rerank_dev.json
    python -m evaluation.rerank_eval --dataset heldout --models mmarco --candidates 30

The dataset is loaded as documents of one user, embedded with the real EmbeddingService and
searched through the real HybridSearchService. Every arm sees the *same* candidates and the same
relevance judgments: the first N results of dense retrieval, or of hybrid (RRF) retrieval, are
scored by the cross-encoder and reordered; results beyond N keep their retrieval order, so
Recall@k for k > N still means something. Candidate recall (is a relevant passage in the first N at
all?) is reported separately because a reranker cannot recover a passage retrieval did not return.

Pair scores do not depend on the arm, so quality is computed from one score per (query, passage)
pair; latency is measured separately, without that cache, on the first `--latency-queries` queries.
Numbers come from the machine that runs this and from small, author-written synthetic datasets.
"""

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
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
from app.reranking.cross_encoder import CrossEncoderReranker, RerankRuntime
from app.reranking.ranking import RerankInput, order_by_score
from app.reranking.types import RerankerConfig
from app.services.hybrid_search_service import HybridSearchService, Retriever
from app.services.search_common import prepare_query
from evaluation.dataset import HELDOUT_DIR, Dataset, load_combined, load_dataset
from evaluation.hybrid_eval import _breakdowns
from evaluation.metrics import QueryResult, strip_neutral
from evaluation.retrieval_eval import _database, _percentile, _settings_for, load_and_embed
from tests.helpers import create_user_in_db

MODELS: dict[str, tuple[str, str]] = {
    "mmarco": (
        "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "1427fd652930e4ba29e8149678df786c240d8825",
    ),
    "bge": ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"),
}
BASE_DEPTH = 50  # how deep the baseline rankings go (as in the Phase 7 evaluation)
ARMS: dict[str, frozenset[Retriever]] = {
    "dense": frozenset({"dense"}),
    "hybrid": frozenset({"dense", "sparse"}),
}


@dataclass
class Retrieved:
    """One query's baseline ranking: passage ids and texts, best first."""

    passage_ids: list[str]
    texts: list[str]


def _top(items: list[str], neutral: tuple[str, ...]) -> list[str]:
    return strip_neutral(items, neutral)


def _rerank_ranking(
    retrieved: Retrieved, scores: dict[str, float], n: int, top_k: int
) -> list[str]:
    """Reorder the first `n` by score; keep the rest in retrieval order."""
    head = [
        RerankInput(pid, text, rank)
        for rank, (pid, text) in enumerate(
            zip(retrieved.passage_ids[:n], retrieved.texts[:n], strict=True), 1
        )
    ]
    flags = [False] * len(head)
    ordered = order_by_score(head, [scores[c.key] for c in head], flags, top_k=len(head))
    return [r.key for r in ordered] + retrieved.passage_ids[n:][: max(0, top_k - n)]


async def retrieve(
    settings: Settings, dataset: Dataset, user_id: uuid.UUID
) -> tuple[dict[str, dict[str, Retrieved]], dict[str, dict[str, float]], dict[str, Any]]:
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = get_provider(settings)
    provider.warmup()
    try:
        chunk_to_passage, embed_seconds, failures = await load_and_embed(
            settings, provider, factory, dataset, user_id
        )
        retrieved: dict[str, dict[str, Retrieved]] = {arm: {} for arm in ARMS}
        latency: dict[str, dict[str, float]] = {arm: {} for arm in ARMS}
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
                for arm, retrievers in ARMS.items():
                    await session.refresh(user)
                    started = time.perf_counter()
                    outcome = await service.search(
                        user, query=query.query, top_k=None, document_ids=None,
                        collection_id=None, retrievers=retrievers, candidate_limit=BASE_DEPTH,
                    )  # fmt: skip
                    latency[arm][query.id] = (time.perf_counter() - started) * 1000
                    retrieved[arm][query.id] = Retrieved(
                        [chunk_to_passage[r.record.chunk_id] for r in outcome.results],
                        [r.record.text for r in outcome.results],
                    )
        meta = {"embed_seconds": round(embed_seconds, 2), "embedding_failures": failures}
        return retrieved, latency, meta
    finally:
        await engine.dispose()


def _make_reranker(name: str, threads: int | None) -> CrossEncoderReranker:
    model, revision = MODELS[name]
    config = RerankerConfig("cross_encoder", model, revision, 512)
    return CrossEncoderReranker(
        config,
        RerankRuntime(device="cpu", batch_size=8, allow_download=False, torch_threads=threads),
    )


def _rss_mb() -> float | None:
    """Resident memory of this process in MB (working set on Windows)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_faults", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t),
                ("peak_paged_pool", ctypes.c_size_t),
                ("paged_pool", ctypes.c_size_t),
                ("peak_non_paged_pool", ctypes.c_size_t),
                ("non_paged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        windll = ctypes.WinDLL("psapi")
        handle = ctypes.WinDLL("kernel32").GetCurrentProcess()
        handle = ctypes.c_void_p(handle)
        windll.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
        ok = windll.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return round(counters.working_set / 1048576, 1) if ok else None
    return None


def _score_pairs(
    reranker: CrossEncoderReranker,
    dataset: Dataset,
    retrieved: dict[str, dict[str, Retrieved]],
    depth: int,
) -> tuple[dict[str, dict[str, float]], int]:
    """One score per (query, passage) over the union of every arm's first `depth` candidates."""
    scores: dict[str, dict[str, float]] = {}
    truncated = 0
    for query in dataset.queries:
        texts: dict[str, str] = {}
        for arm in ARMS:
            item = retrieved[arm][query.id]
            for pid, text in zip(item.passage_ids[:depth], item.texts[:depth], strict=True):
                texts[pid] = text
        ids = list(texts)
        batch = reranker.score(prepare_query(query.query, _EVAL_SETTINGS), [texts[i] for i in ids])
        scores[query.id] = {pid: float(s) for pid, s in zip(ids, batch.scores, strict=True)}
        truncated += sum(batch.truncated)
    return scores, truncated


_EVAL_SETTINGS = Settings(
    _env_file=None, database_url="postgresql+asyncpg://x:x@localhost/x",
    redis_url="redis://localhost", celery_broker_url="redis://localhost",
    celery_result_backend="redis://localhost", jwt_secret_key="x" * 40,
)  # fmt: skip


def _timed(
    reranker: CrossEncoderReranker,
    dataset: Dataset,
    retrieved: dict[str, dict[str, Retrieved]],
    arm: str,
    n: int,
    queries: int,
) -> list[float]:
    times = []
    for query in dataset.queries[:queries]:
        item = retrieved[arm][query.id]
        started = time.perf_counter()
        reranker.score(prepare_query(query.query, _EVAL_SETTINGS), item.texts[:n])
        times.append((time.perf_counter() - started) * 1000)
    return times


def _pct(values: list[float]) -> dict[str, float]:
    return {"p50": round(statistics.median(values), 1), "p95": round(_percentile(values, 0.95), 1)}


def evaluate_model(
    name: str,
    dataset: Dataset,
    lexical_ids: frozenset[str],
    retrieved: dict[str, dict[str, Retrieved]],
    retrieval_ms: dict[str, dict[str, float]],
    candidates: list[int],
    latency_queries: int,
    threads: int | None,
) -> dict[str, Any]:
    reranker = _make_reranker(name, threads)
    rss_before = _rss_mb()
    started = time.perf_counter()
    reranker.warmup()
    load_seconds = time.perf_counter() - started
    rss_after = _rss_mb()
    first = time.perf_counter()
    reranker.score("warm up", ["a short passage"])
    first_call_ms = (time.perf_counter() - first) * 1000
    scores, truncated = _score_pairs(reranker, dataset, retrieved, max(candidates))
    report: dict[str, Any] = {
        "model": MODELS[name][0],
        "revision": MODELS[name][1],
        "device": reranker.device,
        "load_seconds_from_disk_cache": round(load_seconds, 1),
        "first_call_ms": round(first_call_ms, 1),
        "rss_mb_before_after_load": [rss_before, rss_after],
        "truncated_pairs": truncated,
        "by_candidates": {},
    }
    for n in candidates:
        entry: dict[str, Any] = {}
        for arm in ARMS:
            rankings = {
                q.id: _rerank_ranking(retrieved[arm][q.id], scores[q.id], n, BASE_DEPTH)
                for q in dataset.queries
            }
            results = [
                QueryResult(
                    q.id, q.category, q.relevant, tuple(_top(rankings[q.id], q.neutral)), 0.0
                )
                for q in dataset.queries
            ]
            arm_report = _breakdowns(dataset, results, lexical_ids)
            times = _timed(reranker, dataset, retrieved, arm, n, latency_queries)
            retrieval = [retrieval_ms[arm][q.id] for q in dataset.queries[:latency_queries]]
            arm_report["rerank_ms"] = _pct(times)
            arm_report["retrieval_ms"] = _pct(retrieval)
            arm_report["total_ms"] = _pct([a + b for a, b in zip(times, retrieval, strict=True)])
            entry[f"{arm}+rerank"] = arm_report
        report["by_candidates"][str(n)] = entry
    return report


def _baseline(
    dataset: Dataset,
    lexical_ids: frozenset[str],
    retrieved: dict[str, dict[str, Retrieved]],
    retrieval_ms: dict[str, dict[str, float]],
    candidates: list[int],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in ARMS:
        results = [
            QueryResult(
                q.id, q.category, q.relevant,
                tuple(_top(retrieved[arm][q.id].passage_ids, q.neutral)), 0.0,
            )
            for q in dataset.queries
        ]  # fmt: skip
        entry = _breakdowns(dataset, results, lexical_ids)
        entry["retrieval_ms"] = _pct(list(retrieval_ms[arm].values()))
        recall = {}
        for n in candidates:
            answerable = [r for r in results if r.relevant]
            hits = sum(1 for r in answerable if set(r.ranking[:n]) & set(r.relevant))
            recall[str(n)] = round(hits / len(answerable), 3)
        entry["candidate_recall"] = recall
        out[arm] = entry
    return out


def _print(report: dict[str, Any]) -> None:
    def row(label: str, s: dict[str, float]) -> str:
        return (
            f"| {label} | {int(s['queries'])} | {s['recall_at_1']:.3f} | {s['recall_at_3']:.3f} | "
            f"{s['recall_at_10']:.3f} | {s['mrr_at_10']:.3f} | {s['ndcg_at_10']:.3f} |"
        )

    print(f"dataset={report['dataset']} passages={report['passages']} queries={report['queries']}")
    print("candidate recall:", {a: v["candidate_recall"] for a, v in report["baseline"].items()})
    header = (
        "| Arm | Queries | R@1 | R@3 | R@10 | MRR@10 | nDCG@10 |\n|---|---|---|---|---|---|---|"
    )
    for scope in ("overall", "base_set", "lexical_set"):
        if not report["baseline"]["dense"].get(scope):
            continue
        print(f"\n### {scope}\n{header}")
        for arm, entry in report["baseline"].items():
            print(row(arm, entry[scope]))
        for name, model in report["models"].items():
            for n, arms in model["by_candidates"].items():
                for arm, entry in arms.items():
                    print(row(f"{arm} [{name}, N={n}]", entry[scope]))
    print("\n### latency ms (p50 / p95)")
    for arm, entry in report["baseline"].items():
        print(f"- {arm} retrieval: {entry['retrieval_ms']}")
    for name, model in report["models"].items():
        print(
            f"- {name}: load {model['load_seconds_from_disk_cache']} s, first call "
            f"{model['first_call_ms']} ms, RSS {model['rss_mb_before_after_load']}, "
            f"truncated pairs {model['truncated_pairs']}"
        )
        for n, arms in model["by_candidates"].items():
            for arm, entry in arms.items():
                print(f"  - N={n} {arm}: rerank {entry['rerank_ms']} total {entry['total_ms']}")
    print("\n### by category, MRR@10 (hybrid | hybrid+rerank per model/N)")
    categories = sorted(report["baseline"]["hybrid"]["by_category"])
    for category in categories:
        parts = [f"hybrid {report['baseline']['hybrid']['by_category'][category]['mrr_at_10']:.2f}"]
        parts.append(
            f"dense {report['baseline']['dense']['by_category'][category]['mrr_at_10']:.2f}"
        )
        for name, model in report["models"].items():
            for n, arms in model["by_candidates"].items():
                s = arms["hybrid+rerank"]["by_category"][category]["mrr_at_10"]
                d = arms["dense+rerank"]["by_category"][category]["mrr_at_10"]
                parts.append(f"{name}/{n}: h+r {s:.2f} d+r {d:.2f}")
        count = int(report["baseline"]["hybrid"]["by_category"][category]["queries"])
        print(f"- {category} ({count}): " + " | ".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", choices=["dev", "heldout"], default="dev")
    parser.add_argument("--models", nargs="+", choices=sorted(MODELS), default=["mmarco"])
    parser.add_argument("--candidates", nargs="+", type=int, default=[10, 30])
    parser.add_argument("--latency-queries", type=int, default=20)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    import torch

    print(
        f"Python {platform.python_version()} on {platform.platform()} "
        f"({platform.processor()}), torch {torch.__version__}, {torch.get_num_threads()} threads",
        flush=True,
    )
    if args.dataset == "dev":
        dataset, lexical_ids = load_combined()
    else:
        dataset, lexical_ids = load_dataset(HELDOUT_DIR), frozenset()
    url, cleanup = _database()
    try:
        owner = uuid.UUID(create_user_in_db(url, email="eval@example.com"))
        settings = _settings_for(url, None)
        retrieved, retrieval_ms, meta = asyncio.run(retrieve(settings, dataset, owner))
    finally:
        cleanup()
    report: dict[str, Any] = {
        "dataset": args.dataset,
        "passages": len(dataset.passages),
        "queries": len(dataset.queries),
        "answerable": sum(1 for q in dataset.queries if q.relevant),
        "candidates": args.candidates,
        "retrieval": meta,
        "baseline": _baseline(dataset, lexical_ids, retrieved, retrieval_ms, args.candidates),
        "models": {},
    }
    for name in args.models:
        report["models"][name] = evaluate_model(
            name, dataset, lexical_ids, retrieved, retrieval_ms, args.candidates,
            args.latency_queries, args.threads,
        )  # fmt: skip
        if args.json:  # keep partial results if a later model fails
            args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _print(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
