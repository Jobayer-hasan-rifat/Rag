"""Retrieval quality and cost across the real retrievers and reranking depths, with intervals.

For each dataset it loads the corpus as one user's chunked documents (stable passage identity: a
chunk is mapped to its passage through the dataset's fixed ids, never through a chunk UUID that
re-chunking could change), embeds it with the application's embedding service, and then runs every
query through the real services:

- `dense`, `sparse`, `hybrid`: `HybridSearchService` with one or both retrievers, 50 deep;
- `hybrid+rerank@N` for several candidate depths N: the hybrid candidates (N deep) are reordered by
  `RerankService`; the rest of the baseline list is kept behind them, as the reranking evaluation
  has always done, so the metrics of the arms are directly comparable;
- `hybrid+filter` (oracle document filter) and `hybrid+foreign-filter` (a filter that excludes every
  relevant document): the second must never return a result outside its filter (a hard check).

Metrics (see the README): Recall@k, MRR@10, nDCG@10 on answerable questions only (an unanswerable
question has no relevant passage, so those metrics are undefined for it); candidate recall is
reported before reranking; unanswerable questions are reported as a count and, for the dense
retriever, through the answerability AUROC of the top score. Intervals are percentile
bootstraps over questions (Wilson for proportions) and only appear with at least 10 questions.

With the deterministic profile the embedding model is a hashing stand-in and the reranker a word
counter: the numbers then check the harness and the plumbing, not model quality.
"""

import asyncio
import statistics
import time
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
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
from app.reranking.ranking import RerankInput
from app.services.hybrid_search_service import DENSE, SPARSE, HybridSearchService
from app.services.rerank_service import RerankService
from evaluation.dataset import Dataset, EvalQuery
from evaluation.metrics import (
    QueryResult,
    answerability_auroc,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    strip_neutral,
)
from evaluation.retrieval_eval import _EVAL_NAMESPACE, load_and_embed
from evaluation.runmeta import rss_mb
from evaluation.stats import (
    bootstrap_mean_ci,
    latency_summary,
    paired_bootstrap_delta,
    wilson_interval,
)

BASE_DEPTH = 50
RERANK_DEPTHS = (10, 20, 50)
METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr_at_10", "ndcg_at_10")


@dataclass
class Arm:
    name: str
    rankings: dict[str, list[str]]
    latency_ms: list[float]
    top_dense_scores: dict[str, float]
    violations: int = 0  # results outside an applied document filter (must be zero)
    empty: int = 0  # filtered questions that returned nothing (a vacuous filter check)


def per_query_metrics(query: EvalQuery, ranking: list[str]) -> dict[str, float]:
    kept = strip_neutral(ranking, query.neutral)
    return {
        "recall_at_1": recall_at_k(kept, query.relevant, 1),
        "recall_at_3": recall_at_k(kept, query.relevant, 3),
        "recall_at_5": recall_at_k(kept, query.relevant, 5),
        "recall_at_10": recall_at_k(kept, query.relevant, 10),
        "mrr_at_10": reciprocal_rank(kept, query.relevant, 10),
        "ndcg_at_10": ndcg_at_k(kept, query.relevant, 10),
    }


def summarise_arm(queries: Sequence[EvalQuery], rankings: dict[str, list[str]]) -> dict[str, Any]:
    """Mean of every metric over the answerable questions, with intervals and per category."""
    answerable = [q for q in queries if q.relevant]
    values = {q.id: per_query_metrics(q, rankings[q.id]) for q in answerable}

    def block(ids: list[str]) -> dict[str, Any]:
        entry: dict[str, Any] = {"questions": len(ids)}
        for metric in METRICS:
            series = [values[i][metric] for i in ids]
            entry[metric] = round(statistics.fmean(series), 4) if series else None
            interval = bootstrap_mean_ci(series)
            if interval is not None:
                entry[f"{metric}_ci95"] = list(interval)
        hits = sum(1 for i in ids if values[i]["recall_at_1"] > 0)
        interval = wilson_interval(hits, len(ids))
        entry["top1_hits"] = hits
        if interval is not None:
            entry["top1_hit_rate_ci95"] = list(interval)
        return entry

    overall = block([q.id for q in answerable])
    by_category: dict[str, Any] = {}
    for category in sorted({q.category for q in answerable}):
        by_category[category] = block([q.id for q in answerable if q.category == category])
    return {
        "overall": overall,
        "by_category": by_category,
        "per_question": {
            i: {m: round(v, 4) for m, v in vals.items()} for i, vals in values.items()
        },
    }


def candidate_recall(
    queries: Sequence[EvalQuery], rankings: dict[str, list[str]], depth: int
) -> dict[str, Any]:
    """Before reranking: how often a relevant passage is among the first `depth` candidates."""
    answerable = [q for q in queries if q.relevant]
    # the first `depth` results as retrieved (the pool reranking receives); neutral passages are
    # then set aside, exactly as when scoring
    pools = {q.id: strip_neutral(rankings[q.id][:depth], q.neutral) for q in answerable}
    hits = [any(p in q.relevant for p in pools[q.id]) for q in answerable]
    recalls = [recall_at_k(pools[q.id], q.relevant, depth) for q in answerable]
    entry: dict[str, Any] = {
        "depth": depth,
        "questions": len(answerable),
        "any_relevant_in_candidates": round(sum(hits) / len(hits), 4) if hits else None,
        "mean_recall": round(statistics.fmean(recalls), 4) if recalls else None,
    }
    interval = wilson_interval(sum(hits), len(hits))
    if interval is not None:
        entry["any_relevant_ci95"] = list(interval)
    return entry


async def evaluate_dataset(
    settings: Settings,
    name: str,
    dataset: Dataset,
    user_id: uuid.UUID,
    *,
    repeat: int = 1,
    rerank_depths: tuple[int, ...] = RERANK_DEPTHS,
    legacy_ids: bool = False,
) -> dict[str, Any]:
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = get_provider(settings)
    provider.warmup()
    try:
        # datasets share one database; `legacy_ids` reproduces the ids of the earlier tools, whose
        # equal-score ties break by chunk id
        namespace = _EVAL_NAMESPACE if legacy_ids else uuid.uuid5(_EVAL_NAMESPACE, name)
        chunk_to_passage, embed_seconds, failures = await load_and_embed(
            settings, provider, factory, dataset, user_id, namespace
        )
        doc_of_passage = {p.id: p.document_id for p in dataset.passages}
        doc_ids = {d.id: uuid.uuid5(namespace, d.id) for d in dataset.documents}
        arms: dict[str, Arm] = {}
        rerank_cost: dict[int, dict[str, Any]] = {}
        async with factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            service = HybridSearchService(
                session=session, documents=DocumentRepository(session),
                collections=CollectionRepository(session), embeddings=EmbeddingRepository(session),
                lexical=LexicalRepository(session), provider=provider,
                gate=asyncio.Semaphore(1), settings=settings,
            )  # fmt: skip
            reranker = RerankService(asyncio.Semaphore(1), settings)

            async def retrieve(
                query: EvalQuery,
                retrievers: frozenset[Any],
                depth: int,
                documents: list[uuid.UUID] | None = None,
            ) -> tuple[Any, float]:
                await session.refresh(user)  # the service rolls back, which expires it
                started = time.perf_counter()
                outcome = await service.search(
                    user, query=query.query, top_k=None, document_ids=documents,
                    collection_id=None, retrievers=retrievers, candidate_limit=depth,
                )  # fmt: skip
                return outcome, (time.perf_counter() - started) * 1000

            def passages(outcome: Any) -> list[str]:
                return [chunk_to_passage[r.record.chunk_id] for r in outcome.results]

            for arm_name, retrievers in (
                ("dense", frozenset({DENSE})),
                ("sparse", frozenset({SPARSE})),
                ("hybrid", frozenset({DENSE, SPARSE})),
            ):
                arm = Arm(arm_name, {}, [], {})
                for query in dataset.queries:
                    best = None
                    for _ in range(repeat):
                        outcome, ms = await retrieve(query, retrievers, BASE_DEPTH)
                        arm.latency_ms.append(ms)
                        best = outcome
                    assert best is not None
                    arm.rankings[query.id] = passages(best)
                    scores = [r.dense_score for r in best.results if r.dense_score is not None]
                    arm.top_dense_scores[query.id] = scores[0] if scores else -1.0
                arms[arm_name] = arm
            base = arms["hybrid"].rankings

            # oracle document filter: only documents that hold a relevant passage
            arm = Arm("hybrid+filter", {}, [], {})
            for query in dataset.queries:
                wanted = sorted({doc_of_passage[p] for p in query.relevant})
                if not wanted:
                    arm.rankings[query.id] = base[query.id]
                    continue
                outcome, ms = await retrieve(
                    query, frozenset({DENSE, SPARSE}), BASE_DEPTH, [doc_ids[d] for d in wanted]
                )
                arm.latency_ms.append(ms)
                arm.rankings[query.id] = passages(outcome)
                arm.violations += sum(
                    1 for p in arm.rankings[query.id] if doc_of_passage[p] not in wanted
                )
                arm.empty += 0 if arm.rankings[query.id] else 1
            arms["hybrid+filter"] = arm

            # a filter that excludes every relevant document: nothing outside it may come back
            arm = Arm("hybrid+foreign-filter", {}, [], {})
            for query in dataset.queries:
                relevant_docs = {doc_of_passage[p] for p in query.relevant}
                other = next((d.id for d in dataset.documents if d.id not in relevant_docs), None)
                if other is None:
                    arm.rankings[query.id] = []
                    continue
                outcome, ms = await retrieve(
                    query, frozenset({DENSE, SPARSE}), BASE_DEPTH, [doc_ids[other]]
                )
                arm.latency_ms.append(ms)
                arm.rankings[query.id] = passages(outcome)
                arm.violations += sum(
                    1 for p in arm.rankings[query.id] if doc_of_passage[p] != other
                )
            arms["hybrid+foreign-filter"] = arm

            # reranking: same candidates, reordered; cold (first call, model load) and warm
            for depth in rerank_depths:
                arm = Arm(f"hybrid+rerank@{depth}", {}, [], {})
                latencies: list[float] = []
                loads: list[int] = []
                fallbacks = 0
                cold_ms: float | None = None
                for query in dataset.queries:
                    outcome, retrieval_ms = await retrieve(query, frozenset({DENSE, SPARSE}), depth)
                    head = [
                        RerankInput(r.record.chunk_id, r.record.text, r.rank)
                        for r in outcome.results
                    ]
                    started = time.perf_counter()
                    ranked = await reranker.rerank(query.query, head, top_k=len(head))
                    elapsed = (time.perf_counter() - started) * 1000
                    if cold_ms is None and ranked.load_ms:
                        cold_ms = elapsed
                    else:
                        latencies.append(elapsed)
                        arm.latency_ms.append(retrieval_ms + elapsed)
                    loads.append(ranked.load_ms)
                    if not ranked.applied:
                        fallbacks += 1
                        arm.rankings[query.id] = [chunk_to_passage[h.key] for h in head] + base[
                            query.id
                        ][len(head) :]
                        continue
                    order = [chunk_to_passage[item.key] for item in ranked.items]
                    seen = set(order)
                    arm.rankings[query.id] = order + [p for p in base[query.id] if p not in seen]
                    # reranking may only reorder what retrieval returned
                    offered = {chunk_to_passage[h.key] for h in head}
                    arm.violations += sum(1 for p in order if p not in offered)
                arms[arm.name] = arm
                rerank_cost[depth] = {
                    "candidates_per_query": depth,
                    "fallbacks": fallbacks,
                    "first_call_ms_including_load": None if cold_ms is None else round(cold_ms, 1),
                    "model_load_ms_reported": max(loads) if loads else 0,
                    "warm_latency_ms": latency_summary(latencies),
                    "rss_mb_after": rss_mb(),
                }

        report: dict[str, Any] = {
            "dataset": name,
            "documents": len(dataset.documents),
            "passages": len(dataset.passages),
            "questions": len(dataset.queries),
            "answerable": sum(1 for q in dataset.queries if q.relevant),
            "unanswerable": sum(1 for q in dataset.queries if not q.relevant),
            "embed_seconds": round(embed_seconds, 2),
            "embedding_failures": failures,
            "arms": {},
            "candidate_recall_before_rerank": {
                str(depth): candidate_recall(dataset.queries, base, depth) for depth in (10, 20, 50)
            },
            "rerank_cost": {str(d): v for d, v in rerank_cost.items()},
        }
        for arm in arms.values():
            if arm.name == "hybrid+foreign-filter":
                # a check, not a quality arm: no relevant passage can be found by construction
                report["arms"][arm.name] = {
                    "kind": "check",
                    "results_returned": sum(len(r) for r in arm.rankings.values()),
                    "latency_ms": latency_summary(arm.latency_ms),
                    "filter_or_candidate_violations": arm.violations,
                }
                continue
            entry = summarise_arm(dataset.queries, arm.rankings)
            entry["latency_ms"] = latency_summary(arm.latency_ms)
            entry["filter_or_candidate_violations"] = arm.violations
            if arm.name == "hybrid+filter":
                entry["empty_results"] = (
                    arm.empty
                )  # must be 0: a filter that returns nothing proves nothing
            report["arms"][arm.name] = entry
        report["dense_answerability_auroc"] = answerability_auroc(
            [
                QueryResult(q.id, q.category, q.relevant, (), arms["dense"].top_dense_scores[q.id])
                for q in dataset.queries
            ]
        )
        focus = (
            None
            if not rerank_depths
            else (20 if 20 in rerank_depths else rerank_depths[len(rerank_depths) // 2])
        )
        by_arm_category: dict[str, dict[str, float | None]] = defaultdict(dict)
        for category in sorted({q.category for q in dataset.queries if q.relevant}):
            for arm_name in (
                "dense",
                "sparse",
                "hybrid",
                *([f"hybrid+rerank@{focus}"] if focus else []),
            ):
                cat = report["arms"][arm_name]["by_category"].get(category, {})
                by_arm_category[category][arm_name] = cat.get("mrr_at_10")
        report["mrr_at_10_by_category"] = dict(by_arm_category)
        report["rerank_hurts"] = _rerank_effects(dataset.queries, arms, focus) if focus else None
        report["rerank_paired_delta_vs_hybrid"] = {
            str(depth): _paired(dataset.queries, arms["hybrid"], arms[f"hybrid+rerank@{depth}"])
            for depth in rerank_depths
        }
        return report
    finally:
        await engine.dispose()


def _rerank_effects(
    queries: Sequence[EvalQuery], arms: dict[str, Arm], depth: int
) -> dict[str, Any]:
    """Where reranking helps and where it hurts, question by question (reciprocal rank)."""
    before = arms["hybrid"].rankings
    after = arms[f"hybrid+rerank@{depth}"].rankings
    helped: list[str] = []
    hurt: list[str] = []
    for q in queries:
        if not q.relevant:
            continue
        a = reciprocal_rank(strip_neutral(before[q.id], q.neutral), q.relevant, 10)
        b = reciprocal_rank(strip_neutral(after[q.id], q.neutral), q.relevant, 10)
        if b > a:
            helped.append(q.id)
        elif b < a:
            hurt.append(q.id)
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"helped": 0, "hurt": 0})
    cats = {q.id: q.category for q in queries}
    for qid in helped:
        by_category[cats[qid]]["helped"] += 1
    for qid in hurt:
        by_category[cats[qid]]["hurt"] += 1
    return {
        "depth": depth,
        "helped": len(helped),
        "hurt": len(hurt),
        "by_category": dict(sorted(by_category.items())),
        "hurt_question_ids": hurt[:50],
    }


def _paired(queries: Sequence[EvalQuery], before: Arm, after: Arm) -> dict[str, Any]:
    """Mean change per question (reranked minus hybrid) with a paired bootstrap interval: the
    same questions on both sides, so it says whether the gain exceeds question-to-question noise."""
    answerable = [q for q in queries if q.relevant]
    out: dict[str, Any] = {"questions": len(answerable)}
    for metric in ("recall_at_1", "mrr_at_10", "ndcg_at_10"):
        a = [per_query_metrics(q, before.rankings[q.id])[metric] for q in answerable]
        b = [per_query_metrics(q, after.rankings[q.id])[metric] for q in answerable]
        delta, interval = paired_bootstrap_delta(a, b)
        out[metric] = {"delta": delta, "ci95": list(interval) if interval else None}
    return out
