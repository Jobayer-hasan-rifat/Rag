"""Rank fusion. Pure functions: no database, no I/O, fully deterministic.

Reciprocal Rank Fusion (Cormack, Clarke and Buettcher, 2009) combines *rankings*, not scores:
a candidate's fused score is the sum over retrievers of `weight / (k + rank)`, where `rank` starts
at 1. Dense cosine similarities and lexical scores live on unrelated scales and are never added.
"""

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass

DEFAULT_RRF_K = 60
_SCORE_DECIMALS = 12  # equal mathematical scores must compare equal despite float rounding


@dataclass(frozen=True)
class FusedItem[K: Hashable]:
    key: K
    score: float
    ranks: Mapping[str, int]  # retriever name -> 1-based rank, for retrievers that returned it


def reciprocal_rank_fusion[K: Hashable](
    rankings: Mapping[str, Sequence[K]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: Mapping[str, float] | None = None,
    limit: int | None = None,
) -> list[FusedItem[K]]:
    """Fuse ranked candidate lists (best first) into one ranking.

    - A candidate that appears in several lists is deduplicated; its score is the sum of its
      per-list contributions. If a list repeats a key, only its first (best) position counts.
    - An empty or missing list contributes nothing and never invalidates the others.
    - Order is by score, then by the best single rank, then by `str(key)`, so the result is
      identical for identical input regardless of dict ordering or hash seeds.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    weights = weights or {}
    for name, weight in weights.items():
        if weight < 0 or not math.isfinite(weight):
            raise ValueError(f"weight for {name!r} must be a finite number >= 0")

    scores: dict[K, float] = {}
    ranks: dict[K, dict[str, int]] = {}
    for name in sorted(rankings):  # fixed order: float addition is not associative
        weight = weights.get(name, 1.0)
        seen: set[K] = set()
        position = 0
        for key in rankings[name]:
            if key in seen:
                continue
            seen.add(key)
            position += 1
            scores[key] = scores.get(key, 0.0) + weight / (k + position)
            ranks.setdefault(key, {})[name] = position

    ordered = sorted(
        scores,
        key=lambda key: (-round(scores[key], _SCORE_DECIMALS), min(ranks[key].values()), str(key)),
    )
    if limit is not None:
        ordered = ordered[: max(limit, 0)]
    return [FusedItem(key, scores[key], dict(sorted(ranks[key].items()))) for key in ordered]


def overlap[K: Hashable](first: Sequence[K], second: Sequence[K]) -> int:
    """How many candidates two lists share (a diagnostic, not used for scoring)."""
    return len(set(first) & set(second))
