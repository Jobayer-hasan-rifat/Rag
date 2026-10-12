"""Retrieval metrics. Pure functions over ranked passage ids, so they are easy to test.

Conventions
- A ranking is a list of passage ids, best first, covering the whole corpus.
- `neutral` passages are removed from the ranking before scoring: retrieving one is neither a
  hit nor a miss (used for passages that are acceptable but not the intended answer).
- Relevance is binary. nDCG@k therefore uses gain 1 for every relevant passage.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass


def strip_neutral(ranking: Sequence[str], neutral: Sequence[str]) -> list[str]:
    skipped = set(neutral)
    return [passage for passage in ranking if passage not in skipped]


def recall_at_k(ranking: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if not relevant:
        raise ValueError("recall is undefined without relevant passages")
    return len(set(ranking[:k]) & set(relevant)) / len(set(relevant))


def reciprocal_rank(
    ranking: Sequence[str], relevant: Sequence[str], cutoff: int | None = None
) -> float:
    wanted = set(relevant)
    limit = len(ranking) if cutoff is None else cutoff
    for position, passage in enumerate(ranking[:limit], start=1):
        if passage in wanted:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranking: Sequence[str], relevant: Sequence[str], k: int) -> float:
    wanted = set(relevant)
    if not wanted:
        raise ValueError("nDCG is undefined without relevant passages")
    dcg = sum(
        1.0 / math.log2(rank + 1) for rank, p in enumerate(ranking[:k], start=1) if p in wanted
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(wanted), k) + 1))
    return dcg / ideal


def auroc(positive: Sequence[float], negative: Sequence[float]) -> float:
    """P(random positive scores above random negative); ties count one half."""
    if not positive or not negative:
        raise ValueError("AUROC needs both positive and negative scores")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    category: str
    relevant: tuple[str, ...]
    ranking: tuple[str, ...]  # neutral passages already removed
    top_score: float  # score of the best passage (before neutral removal)


@dataclass(frozen=True)
class Summary:
    queries: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr_at_10: float
    ndcg_at_10: float


def summarise(results: Sequence[QueryResult]) -> Summary | None:
    scored = [r for r in results if r.relevant]
    if not scored:
        return None
    count = len(scored)

    def mean(values: list[float]) -> float:
        return sum(values) / count

    return Summary(
        queries=count,
        recall_at_1=mean([recall_at_k(r.ranking, r.relevant, 1) for r in scored]),
        recall_at_3=mean([recall_at_k(r.ranking, r.relevant, 3) for r in scored]),
        recall_at_5=mean([recall_at_k(r.ranking, r.relevant, 5) for r in scored]),
        recall_at_10=mean([recall_at_k(r.ranking, r.relevant, 10) for r in scored]),
        mrr_at_10=mean([reciprocal_rank(r.ranking, r.relevant, 10) for r in scored]),
        ndcg_at_10=mean([ndcg_at_k(r.ranking, r.relevant, 10) for r in scored]),
    )


def answerability_auroc(results: Sequence[QueryResult]) -> float | None:
    """How well the best-passage score separates answerable from unanswerable queries."""
    positive = [r.top_score for r in results if r.relevant]
    negative = [r.top_score for r in results if not r.relevant]
    if not positive or not negative:
        return None
    return auroc(positive, negative)
