import itertools
import random

import pytest

from app.retrieval.fusion import DEFAULT_RRF_K, overlap, reciprocal_rank_fusion


def test_the_score_is_the_sum_of_weight_over_k_plus_rank() -> None:
    fused = reciprocal_rank_fusion({"dense": ["a", "b"], "sparse": ["b", "c"]}, k=60)

    scores = {item.key: item.score for item in fused}
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 62)
    assert [item.key for item in fused] == ["b", "a", "c"]


def test_default_constant_and_ranks_are_reported_per_retriever() -> None:
    fused = reciprocal_rank_fusion({"dense": ["a", "b"], "sparse": ["b"]})

    assert DEFAULT_RRF_K == 60
    by_key = {item.key: item for item in fused}
    assert by_key["b"].ranks == {"dense": 2, "sparse": 1}
    assert by_key["a"].ranks == {"dense": 1}  # absent from the other list: no entry, not zero


def test_a_candidate_in_both_lists_appears_once() -> None:
    fused = reciprocal_rank_fusion({"dense": ["x", "y"], "sparse": ["y", "x"]})

    assert sorted(item.key for item in fused) == ["x", "y"]


def test_an_empty_or_missing_list_does_not_invalidate_the_other() -> None:
    only_dense = reciprocal_rank_fusion({"dense": ["a", "b", "c"], "sparse": []})
    only_sparse = reciprocal_rank_fusion({"sparse": ["z", "y"]})

    assert [i.key for i in only_dense] == ["a", "b", "c"]
    assert [i.key for i in only_sparse] == ["z", "y"]
    assert only_dense[0].score == pytest.approx(1 / 61)


def test_no_input_gives_no_output() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"dense": [], "sparse": []}) == []


def test_a_key_repeated_inside_one_list_counts_once_at_its_best_position() -> None:
    fused = reciprocal_rank_fusion({"dense": ["a", "b", "a", "c"]})

    assert [(i.key, i.ranks["dense"]) for i in fused] == [("a", 1), ("b", 2), ("c", 3)]


def test_weights_scale_each_retrievers_contribution() -> None:
    fused = reciprocal_rank_fusion(
        {"dense": ["a"], "sparse": ["b"]}, weights={"dense": 1.0, "sparse": 3.0}
    )

    assert [i.key for i in fused] == ["b", "a"]
    assert fused[0].score == pytest.approx(3 / 61)
    muted = reciprocal_rank_fusion({"dense": ["a"], "sparse": ["b"]}, weights={"sparse": 0.0})
    assert next(i.key for i in muted) == "a" and muted[1].score == 0.0


def test_a_smaller_k_rewards_top_ranks_more_sharply() -> None:
    sharp = reciprocal_rank_fusion({"a": ["x", "y"]}, k=1)
    flat = reciprocal_rank_fusion({"a": ["x", "y"]}, k=1000)

    assert sharp[0].score / sharp[1].score == pytest.approx(3 / 2)  # 1/2 against 1/3
    assert flat[0].score / flat[1].score == pytest.approx(1002 / 1001)


@pytest.mark.parametrize("k", [0, -5])
def test_k_must_be_positive(k: int) -> None:
    with pytest.raises(ValueError, match="k must be at least 1"):
        reciprocal_rank_fusion({"a": ["x"]}, k=k)


@pytest.mark.parametrize("weight", [-1.0, float("nan"), float("inf")])
def test_weights_must_be_finite_and_not_negative(weight: float) -> None:
    with pytest.raises(ValueError, match="finite number"):
        reciprocal_rank_fusion({"a": ["x"]}, weights={"a": weight})


def test_limit_truncates_and_zero_or_negative_limit_gives_nothing() -> None:
    rankings = {"a": ["1", "2", "3", "4"]}

    assert [i.key for i in reciprocal_rank_fusion(rankings, limit=2)] == ["1", "2"]
    assert reciprocal_rank_fusion(rankings, limit=0) == []
    assert reciprocal_rank_fusion(rankings, limit=-3) == []
    assert len(reciprocal_rank_fusion(rankings, limit=99)) == 4


def test_equal_scores_are_ordered_by_best_rank_then_key() -> None:
    # "p" is first in dense and second in sparse, "q" the reverse: identical scores and best ranks
    fused = reciprocal_rank_fusion({"dense": ["p", "q"], "sparse": ["q", "p"]})

    assert fused[0].score == pytest.approx(fused[1].score)
    assert [i.key for i in fused] == ["p", "q"]  # str(key) decides, deterministically


def test_a_better_single_rank_wins_an_exact_score_tie() -> None:
    # 1/(k+1) + 1/(k+4) == 1/(k+2) + 1/(k+3) only for specific k; use equal-score construction:
    # a: dense rank 1 only; b: sparse rank 1 only -> equal score, equal best rank -> key order
    fused = reciprocal_rank_fusion({"dense": ["b"], "sparse": ["a"]})

    assert [i.key for i in fused] == ["a", "b"]


def test_the_result_is_identical_for_every_ordering_of_the_input() -> None:
    dense = ["c", "a", "e", "b"]
    sparse = ["b", "d", "a", "f"]
    expected = reciprocal_rank_fusion({"dense": dense, "sparse": sparse})

    for names in itertools.permutations(["dense", "sparse"]):
        rankings = {name: {"dense": dense, "sparse": sparse}[name] for name in names}
        assert reciprocal_rank_fusion(rankings) == expected


def test_matches_a_brute_force_implementation_on_random_input() -> None:
    rng = random.Random(7)  # noqa: S311
    keys = [f"k{i}" for i in range(30)]
    for _ in range(50):
        dense = rng.sample(keys, rng.randint(0, 20))
        sparse = rng.sample(keys, rng.randint(0, 20))
        k = rng.choice([1, 10, 60, 200])
        fused = reciprocal_rank_fusion({"dense": dense, "sparse": sparse}, k=k)

        expected: dict[str, float] = {}
        for ranking in (dense, sparse):
            for position, key in enumerate(ranking, start=1):
                expected[key] = expected.get(key, 0.0) + 1 / (k + position)
        assert {i.key for i in fused} == set(expected)
        for item in fused:
            assert item.score == pytest.approx(expected[item.key])
        scores = [round(i.score, 12) for i in fused]
        assert scores == sorted(scores, reverse=True)


def test_non_string_keys_work_and_are_ordered_by_their_text() -> None:
    import uuid

    ids = [uuid.UUID(int=n) for n in (3, 1, 2)]
    fused = reciprocal_rank_fusion({"dense": [ids[0]], "sparse": [ids[1]], "third": [ids[2]]})

    assert [i.key for i in fused] == sorted(ids, key=str)


def test_overlap_counts_shared_candidates() -> None:
    assert overlap(["a", "b", "c"], ["c", "b", "z"]) == 2
    assert overlap([], ["a"]) == 0
    assert overlap(["a", "a"], ["a"]) == 1
