"""Ordering logic of reranking: pure functions, no model, no database.

Candidates keep the rank and score they had from retrieval; the reranker's raw score only
decides the new order. Ties are broken by the original retrieval rank, then by key text, so the
result is deterministic for a given set of scores.
"""

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankInput[K: Hashable]:
    key: K
    text: str
    retrieval_rank: int  # 1-based position from the retrieval stage


@dataclass(frozen=True)
class Reranked[K: Hashable]:
    key: K
    rank: int  # 1-based position after reranking
    score: float  # the reranker's raw score
    retrieval_rank: int
    truncated: bool


def unique_candidates[K: Hashable](candidates: Sequence[RerankInput[K]]) -> list[RerankInput[K]]:
    """Drop repeated keys, keeping the first (best retrieval rank) occurrence."""
    seen: set[K] = set()
    kept: list[RerankInput[K]] = []
    for candidate in candidates:
        if candidate.key not in seen:
            seen.add(candidate.key)
            kept.append(candidate)
    return kept


def order_by_score[K: Hashable](
    candidates: Sequence[RerankInput[K]],
    scores: Sequence[float],
    truncated: Sequence[bool],
    *,
    top_k: int,
) -> list[Reranked[K]]:
    """Order `candidates` by descending raw score and keep the best `top_k`.

    `scores` and `truncated` are parallel to `candidates`. Raises ValueError if the lengths
    differ or a score is not finite: a missing or broken score must never turn into a rank.
    """
    if len(scores) != len(candidates) or len(truncated) != len(candidates):
        raise ValueError("one score and one truncation flag are needed per candidate")
    if any(not math.isfinite(score) for score in scores):
        raise ValueError("scores must be finite")
    if top_k <= 0:
        return []
    order = sorted(
        range(len(candidates)),
        key=lambda i: (-scores[i], candidates[i].retrieval_rank, str(candidates[i].key)),
    )
    return [
        Reranked(
            key=candidates[i].key,
            rank=position,
            score=float(scores[i]),
            retrieval_rank=candidates[i].retrieval_rank,
            truncated=bool(truncated[i]),
        )
        for position, i in enumerate(order[:top_k], start=1)
    ]
