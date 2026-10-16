"""Small, deterministic statistics for evaluation reports (no numpy, no randomness unless seeded).

Why these and not more: the datasets are small (tens of questions), so a point estimate without an
interval overstates what is known. Proportions get a Wilson interval; means over per-question
scores get a percentile bootstrap; differences between two systems on the same questions get a
paired bootstrap. Below `MIN_SAMPLES_FOR_CI` questions no interval is reported at all, because a
bootstrap of a handful of values only looks precise.
"""

import math
import random
import statistics
from collections.abc import Sequence

MIN_SAMPLES_FOR_CI = 10
DEFAULT_RESAMPLES = 2000
DEFAULT_SEED = 20261016


def wilson_interval(
    successes: int, total: int, confidence: float = 0.95
) -> tuple[float, float] | None:
    """Wilson score interval for a proportion; None when there are too few trials."""
    if total < MIN_SAMPLES_FOR_CI:
        return None
    z = _z(confidence)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval of the mean; None when there are too few values."""
    if len(values) < MIN_SAMPLES_FOR_CI:
        return None
    rng = random.Random(seed)  # noqa: S311  (seeded resampling, not security)
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(resamples))
    low = means[max(0, math.ceil(((1 - confidence) / 2) * resamples) - 1)]
    high = means[min(resamples - 1, math.ceil((1 - (1 - confidence) / 2) * resamples) - 1)]
    return (round(low, 4), round(high, 4))


def paired_bootstrap_delta(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> tuple[float, tuple[float, float] | None]:
    """Mean of (candidate - baseline) over the same questions, with a paired bootstrap interval."""
    if len(baseline) != len(candidate):
        raise ValueError("paired samples must have the same length")
    if not baseline:
        raise ValueError("no samples")
    deltas = [c - b for b, c in zip(baseline, candidate, strict=True)]
    return round(statistics.fmean(deltas), 4), bootstrap_mean_ci(
        deltas, confidence=confidence, resamples=resamples, seed=seed
    )


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile (fraction in [0, 1])."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


def latency_summary(values: Sequence[float]) -> dict[str, float | int]:
    """Median and tail percentiles. p99 is only meaningful with many samples, so it is omitted
    below 100 (the report says how many samples there were)."""
    if not values:
        return {"n": 0}
    summary: dict[str, float | int] = {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "p95": round(percentile(values, 0.95), 2),
        "max": round(max(values), 2),
    }
    if len(values) >= 100:
        summary["p99"] = round(percentile(values, 0.99), 2)
    return summary


def _z(confidence: float) -> float:
    table = {0.90: 1.6449, 0.95: 1.96, 0.99: 2.5758}
    if confidence not in table:
        raise ValueError("confidence must be 0.90, 0.95 or 0.99")
    return table[confidence]
