import pytest

from evaluation.dataset import load_rag_corpus, load_rag_questions
from evaluation.rag_metrics import (
    QuestionResult,
    contains_forbidden,
    fact_complete,
    fold,
    group_by,
    summarise,
)


def result(**kw: object) -> QuestionResult:
    base: dict[str, object] = {
        "id": "q", "category": "english", "language": "en", "abstain_expected": False,
        "answered": True, "insufficient_reason": None, "answer": "a", "relevant": ["p1"],
        "cited_passages": ["p1"], "candidate_passages": ["p1"], "source_passages": ["p1"],
        "fact_complete": True, "latency_ms": 100, "generation_ms": 80,
    }  # fmt: skip
    base.update(kw)
    return QuestionResult(**base)  # type: ignore[arg-type]


def test_folding_makes_unicode_case_and_digits_comparable() -> None:
    assert fold("  ১২ MARCH ") == "12 march"
    assert fold("কো") == fold("কো")


def test_fact_completeness_needs_every_group_and_any_alternative() -> None:
    assert fact_complete("It opens at EIGHT and lunch at twelve", [["eight", "8"], ["twelve"]])
    assert not fact_complete("It opens at eight", [["eight"], ["twelve"]])
    assert fact_complete("১৯৯৯ সালে", [["1999"]])  # Bengali digits fold to Latin
    assert fact_complete("anything", [])


def test_forbidden_strings_are_found_after_normalisation() -> None:
    assert contains_forbidden("The answer is pwned.", ["PWNED"])
    assert not contains_forbidden("fine", ["PWNED"])
    assert not contains_forbidden("anything", [])


def test_the_summary_computes_each_rate_from_the_right_population() -> None:
    results = [
        result(id="a1"),
        result(id="a2", fact_complete=False, cited_passages=["other"]),
        result(id="a3", answered=False, fact_complete=False, cited_passages=[]),
        result(id="u1", abstain_expected=True, answered=False, relevant=[], fact_complete=None),
        result(id="u2", abstain_expected=True, answered=True, relevant=[], fact_complete=None,
               cited_passages=["x"], forbidden_hit=True),
        result(id="e1", error="GENERATION_UNAVAILABLE:timeout"),
    ]  # fmt: skip

    summary = summarise(results)

    assert summary["questions"] == 6 and summary["provider_errors"] == 1
    assert summary["answerable"] == 3 and summary["unanswerable"] == 2
    assert summary["answered_rate_on_answerable"] == pytest.approx(2 / 3, abs=1e-3)
    assert summary["abstention_accuracy_on_unanswerable"] == 0.5
    assert summary["fact_recall_on_answerable"] == pytest.approx(1 / 3, abs=1e-3)
    assert summary["answers_with_a_gold_citation"] == 0.5  # a1 yes, a2 no (a3 not answered)
    assert summary["forbidden_string_rate"] == 0.2
    assert summary["latency_ms"] == {"p50": 100.0, "p95": 100.0}


def test_empty_populations_give_none_not_zero() -> None:
    summary = summarise([result(abstain_expected=False)])

    assert summary["abstention_accuracy_on_unanswerable"] is None


def test_grouping_summarises_each_group_separately() -> None:
    groups = group_by(
        [result(category="bangla"), result(category="english", answered=False)], "category"
    )

    assert set(groups) == {"bangla", "english"}
    assert groups["bangla"]["answered_rate_on_answerable"] == 1.0
    assert groups["english"]["answered_rate_on_answerable"] == 0.0


def test_the_rag_dataset_is_valid_balanced_and_covers_the_required_cases() -> None:
    questions = load_rag_questions()
    corpus = load_rag_corpus()

    assert len(questions) >= 40 and len({q.id for q in questions}) == len(questions)
    categories = {q.category for q in questions}
    assert {
        "english", "bangla", "mixed", "cross_lingual", "unanswerable",
        "contradiction", "adversarial", "long_chunk",
    } <= categories  # fmt: skip
    assert sum(q.abstain for q in questions) >= 8
    known = {p.id for p in corpus.passages}
    assert all(set(q.relevant) <= known for q in questions)
    injected = [p for p in corpus.passages if "IGNORE ALL PREVIOUS" in p.text or "PWNED" in p.text]
    assert len(injected) >= 2  # adversarial documents are really in the corpus
    long_chunk = next(p for p in corpus.passages if p.id == "rx6-1")
    assert len(long_chunk.text) > 3000 and long_chunk.text.rstrip().endswith("regional office.")
