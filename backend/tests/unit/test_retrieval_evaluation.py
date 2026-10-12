import json
import math
from pathlib import Path

import numpy as np
import pytest

from evaluation.dataset import (
    DATA_DIR,
    Dataset,
    EvalDocument,
    EvalQuery,
    Passage,
    load_dataset,
    validate,
)
from evaluation.metrics import (
    QueryResult,
    answerability_auroc,
    auroc,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    strip_neutral,
    summarise,
)
from evaluation.retrieval_eval import evaluate_memory
from tests.embedding_fakes import HashProvider
from tests.unit.test_embedding_providers import make_config

# --- metrics ---------------------------------------------------------------------------------


def test_recall_counts_the_share_of_relevant_passages_in_the_top_k() -> None:
    ranking = ["a", "b", "c", "d"]

    assert recall_at_k(ranking, ["a"], 1) == 1.0
    assert recall_at_k(ranking, ["c"], 2) == 0.0
    assert recall_at_k(ranking, ["a", "c", "z"], 3) == pytest.approx(2 / 3)
    with pytest.raises(ValueError):
        recall_at_k(ranking, [], 3)


def test_reciprocal_rank_uses_the_first_relevant_position_and_an_optional_cutoff() -> None:
    ranking = ["a", "b", "c", "d"]

    assert reciprocal_rank(ranking, ["c", "d"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranking, ["a"]) == 1.0
    assert reciprocal_rank(ranking, ["z"]) == 0.0
    assert reciprocal_rank(ranking, ["d"], cutoff=3) == 0.0


def test_ndcg_matches_hand_computed_values() -> None:
    ranking = ["x", "a", "y", "b"]

    # relevant {a, b} at ranks 2 and 4: DCG = 1/log2(3) + 1/log2(5); ideal = 1 + 1/log2(3)
    expected = (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3))
    assert ndcg_at_k(ranking, ["a", "b"], 4) == pytest.approx(expected)
    assert ndcg_at_k(["a", "b"], ["a", "b"], 2) == pytest.approx(1.0)
    assert ndcg_at_k(["x", "y"], ["a"], 2) == 0.0
    assert ndcg_at_k(["a", "x"], ["a", "b"], 1) == pytest.approx(1.0)  # ideal is cut at k too


def test_neutral_passages_are_removed_before_ranking() -> None:
    assert strip_neutral(["n", "a", "b"], ["n"]) == ["a", "b"]
    assert reciprocal_rank(strip_neutral(["n", "a"], ["n"]), ["a"]) == 1.0


def test_auroc_handles_perfect_reversed_and_tied_scores() -> None:
    assert auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert auroc([0.1], [0.9]) == 0.0
    assert auroc([0.5], [0.5]) == 0.5
    with pytest.raises(ValueError):
        auroc([], [1.0])


def test_summary_averages_over_answerable_queries_only() -> None:
    results = [
        QueryResult("q1", "c", ("a",), ("a", "b"), 0.9),
        QueryResult("q2", "c", ("b",), ("a", "b"), 0.8),
        QueryResult("q3", "c", (), ("a", "b"), 0.2),
    ]

    summary = summarise(results)

    assert summary is not None and summary.queries == 2
    assert summary.recall_at_1 == 0.5 and summary.recall_at_3 == 1.0
    assert summary.mrr_at_10 == pytest.approx(0.75)
    assert answerability_auroc(results) == 1.0
    assert summarise([results[2]]) is None and answerability_auroc(results[:2]) is None


# --- dataset ---------------------------------------------------------------------------------


def test_the_shipped_dataset_loads_and_has_the_documented_shape() -> None:
    dataset = load_dataset()

    assert (
        len(dataset.documents) == 12 and len(dataset.passages) == 48 and len(dataset.queries) == 52
    )
    languages = {d.language: 0 for d in dataset.documents}
    for d in dataset.documents:
        languages[d.language] += 1
    assert languages == {"en": 4, "bn": 4, "mixed": 4}
    categories = {q.category for q in dataset.queries}
    assert categories == {
        "english",
        "bangla",
        "mixed",
        "punctuation_unicode",
        "cross_lingual",
        "confusable",
        "no_answer",
    }
    assert sum(1 for q in dataset.queries if not q.relevant) == 6


def test_every_passage_stays_small_enough_to_remain_one_chunk() -> None:
    assert all(len(p.text) <= 300 for p in load_dataset().passages)


def test_every_query_has_a_relevant_passage_unless_it_is_a_no_answer_query() -> None:
    for q in load_dataset().queries:
        assert bool(q.relevant) == (q.category != "no_answer"), q.id


def test_bangla_documents_really_contain_bangla_and_english_ones_do_not() -> None:
    def has_bengali(text: str) -> bool:
        return any("ঀ" <= ch <= "৿" for ch in text)

    for p in load_dataset().passages:
        if p.language == "bn":
            assert has_bengali(p.text), p.id
        elif p.language == "en":
            assert not has_bengali(p.text), p.id
        else:
            assert has_bengali(p.text) and any(c.isascii() and c.isalpha() for c in p.text), p.id


def _dataset(passages: list[str], queries: list[EvalQuery]) -> Dataset:
    items = tuple(Passage(pid, "d", "D", "en", "H", "text") for pid in passages)
    return Dataset((EvalDocument("d", "D", "en", items),), tuple(queries))


@pytest.mark.parametrize(
    ("passages", "query", "message"),
    [
        (["p1", "p1"], EvalQuery("q", "c", "text", ("p1",), ()), "duplicate passage"),
        (["p1"], EvalQuery("q", "c", "text", ("zzz",), ()), "unknown passages"),
        (["p1"], EvalQuery("q", "c", "text", ("p1",), ("zzz",)), "unknown passages"),
        (["p1"], EvalQuery("q", "c", "text", ("p1",), ("p1",)), "both relevant and neutral"),
        (["p1"], EvalQuery("q", "c", "   ", ("p1",), ()), "is empty"),
    ],
)
def test_malformed_datasets_are_rejected(
    passages: list[str], query: EvalQuery, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate(_dataset(passages, [query]))


def test_duplicate_query_ids_are_rejected() -> None:
    query = EvalQuery("q", "c", "text", ("p1",), ())

    with pytest.raises(ValueError, match="duplicate query"):
        validate(_dataset(["p1"], [query, query]))


def test_loading_from_another_directory_works(tmp_path: Path) -> None:
    corpus = {
        "documents": [
            {
                "id": "d",
                "title": "T",
                "language": "en",
                "sections": [{"heading": "h", "passage": {"id": "p", "text": "t"}}],
            }
        ]
    }
    queries = {"queries": [{"id": "q", "category": "english", "query": "t", "relevant": ["p"]}]}
    (tmp_path / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
    (tmp_path / "queries.json").write_text(json.dumps(queries), encoding="utf-8")

    dataset = load_dataset(tmp_path)

    assert [p.id for p in dataset.passages] == ["p"] and dataset.queries[0].neutral == ()
    assert DATA_DIR.name == "retrieval"


# --- the runner, with a deterministic provider ---------------------------------------------


def test_the_memory_runner_scores_a_lexical_provider_sensibly() -> None:
    dataset = load_dataset()
    provider = HashProvider(make_config(provider="test_hash"))

    report = evaluate_memory(provider, dataset, batch_size=8)

    assert report.passages == 48 and report.queries == 52 and report.unanswerable == 6
    assert report.embedding_failures == 0 and report.passages_per_second > 0
    assert 0.0 <= report.overall["recall_at_1"] <= report.overall["recall_at_10"] <= 1.0
    assert report.overall["mrr_at_10"] >= report.overall["recall_at_1"]
    # a word-overlap provider cannot match across languages, but it should find same-language facts
    assert report.by_category["english"]["recall_at_3"] >= 0.5
    assert math.isfinite(report.answerability_auroc or 0.0)
    assert set(report.by_category) >= {"english", "bangla", "mixed", "cross_lingual"}
    assert isinstance(np.float32(report.query_ms_median), np.float32)


def test_the_lexical_companion_dataset_is_valid_and_merges_without_id_clashes() -> None:
    from evaluation.dataset import load_combined

    dataset, lexical_ids = load_combined()

    assert lexical_ids and lexical_ids < {q.id for q in dataset.queries}
    assert len({p.id for p in dataset.passages}) == len(dataset.passages)
    categories = {q.category for q in dataset.queries if q.id in lexical_ids}
    assert {"identifier", "phrase", "paraphrase", "bangla_inflection", "no_answer"} <= categories
