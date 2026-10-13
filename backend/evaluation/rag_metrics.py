"""Deterministic metrics for the answer-generation evaluation. Pure functions, no model.

Nothing here judges meaning: fact recall is substring matching against author-written expected
facts, abstention is read from the pipeline's own flag, and citation checks compare identifiers.
Whether an answer is faithful in a way these checks cannot see is a human judgement, recorded
separately and labelled as such.
"""

import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

_BENGALI_DIGITS = {0x09E6 + i: str(i) for i in range(10)}


def fold(text: str) -> str:
    """NFC, case-folded, Bengali digits as Latin digits, whitespace collapsed."""
    normal = unicodedata.normalize("NFC", text).casefold().translate(_BENGALI_DIGITS)
    return " ".join(normal.split())


def fact_complete(answer: str, expected: list[list[str]]) -> bool:
    """Every group of alternatives has at least one member in the answer."""
    folded = fold(answer)
    return all(any(fold(option) in folded for option in group) for group in expected)


def contains_forbidden(answer: str, forbidden: list[str]) -> bool:
    folded = fold(answer)
    return any(fold(item) in folded for item in forbidden)


@dataclass
class QuestionResult:
    id: str
    category: str
    language: str  # bn | en | mixed, from the question text
    abstain_expected: bool
    answered: bool
    insufficient_reason: str | None
    answer: str
    cited_passages: list[str] = field(default_factory=list)
    relevant: list[str] = field(default_factory=list)
    candidate_passages: list[str] = field(default_factory=list)
    source_passages: list[str] = field(default_factory=list)
    fact_complete: bool | None = None
    forbidden_hit: bool = False
    labels_valid: bool = True
    citation_metadata_ok: bool = True
    removed: int = 0  # sentences or labels the policy layer removed
    error: str | None = None
    latency_ms: int = 0
    generation_ms: int = 0
    retrieval_ms: int = 0
    context_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts: int = 0


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def summarise(results: list[QuestionResult]) -> dict[str, object]:
    ok = [r for r in results if r.error is None]
    answerable = [r for r in ok if not r.abstain_expected]
    unanswerable = [r for r in ok if r.abstain_expected]
    answered = [r for r in ok if r.answered]
    summary: dict[str, object] = {
        "questions": len(results),
        "provider_errors": sum(1 for r in results if r.error is not None),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answered_rate_on_answerable": _rate(sum(r.answered for r in answerable), len(answerable)),
        "abstention_accuracy_on_unanswerable": _rate(
            sum(not r.answered for r in unanswerable), len(unanswerable)
        ),
        "fact_recall_on_answerable": _rate(
            sum(bool(r.fact_complete) for r in answerable), len(answerable)
        ),
        "citation_label_validity": _rate(sum(r.labels_valid for r in answered), len(answered)),
        "citation_metadata_matches_database": _rate(
            sum(r.citation_metadata_ok for r in answered), len(answered)
        ),
        "answers_with_a_gold_citation": _rate(
            sum(bool(set(r.cited_passages) & set(r.relevant)) for r in answerable if r.answered),
            sum(1 for r in answerable if r.answered),
        ),
        "citation_precision": _rate(
            sum(len(set(r.cited_passages) & set(r.relevant)) for r in answerable if r.answered),
            sum(len(set(r.cited_passages)) for r in answerable if r.answered),
        ),
        "answers_without_any_citation": sum(1 for r in answered if not r.cited_passages),
        "forbidden_string_rate": _rate(sum(r.forbidden_hit for r in ok), len(ok)),
        "policy_removals_total": sum(r.removed for r in ok),
        "answers_needing_a_retry": sum(1 for r in ok if r.attempts > 1),
        "candidate_recall_on_answerable": _rate(
            sum(
                bool(set(r.candidate_passages) & set(r.relevant)) for r in answerable if r.relevant
            ),
            sum(1 for r in answerable if r.relevant),
        ),
        "source_recall_on_answerable": _rate(
            sum(bool(set(r.source_passages) & set(r.relevant)) for r in answerable if r.relevant),
            sum(1 for r in answerable if r.relevant),
        ),
    }
    if ok:
        totals = [float(r.latency_ms) for r in ok]
        gens = [float(r.generation_ms) for r in ok if r.generation_ms]
        summary["latency_ms"] = {
            "p50": round(statistics.median(totals), 1),
            "p95": round(_percentile(totals, 0.95), 1),
        }
        if gens:
            summary["generation_ms"] = {
                "p50": round(statistics.median(gens), 1),
                "p95": round(_percentile(gens, 0.95), 1),
            }
        inputs = [r.input_tokens for r in ok if r.input_tokens is not None]
        outputs = [r.output_tokens for r in ok if r.output_tokens is not None]
        summary["input_tokens_mean"] = round(statistics.fmean(inputs), 1) if inputs else None
        summary["output_tokens_mean"] = round(statistics.fmean(outputs), 1) if outputs else None
    return summary


def group_by(results: list[QuestionResult], key: str) -> dict[str, dict[str, object]]:
    groups: dict[str, list[QuestionResult]] = defaultdict(list)
    for result in results:
        groups[str(getattr(result, key))].append(result)
    return {name: summarise(items) for name, items in sorted(groups.items())}
