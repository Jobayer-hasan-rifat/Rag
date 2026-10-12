import itertools
import random
import uuid

import pytest

from app.reranking.ranking import RerankInput, order_by_score, unique_candidates


def cand(key: str, rank: int, text: str = "t") -> RerankInput[str]:
    return RerankInput(key, text, rank)


def test_higher_raw_score_ranks_first_and_retrieval_rank_is_kept() -> None:
    candidates = [cand("a", 1), cand("b", 2), cand("c", 3)]

    ranked = order_by_score(candidates, [0.1, 2.5, -1.0], [False, False, True], top_k=3)

    assert [r.key for r in ranked] == ["b", "a", "c"]
    assert [r.rank for r in ranked] == [1, 2, 3]
    assert [r.retrieval_rank for r in ranked] == [2, 1, 3]
    assert [r.score for r in ranked] == [2.5, 0.1, -1.0]  # raw: no softmax, no rescaling
    assert [r.truncated for r in ranked] == [False, False, True]


def test_top_k_keeps_the_best_and_nonpositive_top_k_gives_nothing() -> None:
    candidates = [cand(str(i), i + 1) for i in range(5)]
    scores = [0.0, 4.0, 3.0, 2.0, 1.0]
    flags = [False] * 5

    assert [r.key for r in order_by_score(candidates, scores, flags, top_k=2)] == ["1", "2"]
    assert order_by_score(candidates, scores, flags, top_k=0) == []
    assert order_by_score(candidates, scores, flags, top_k=-3) == []
    assert len(order_by_score(candidates, scores, flags, top_k=99)) == 5


def test_equal_scores_fall_back_to_retrieval_rank_then_key() -> None:
    candidates = [cand("z", 3), cand("a", 2), cand("m", 2), cand("b", 1)]

    ranked = order_by_score(candidates, [1.0] * 4, [False] * 4, top_k=4)

    assert [r.key for r in ranked] == ["b", "a", "m", "z"]  # rank 1, then rank 2 by key, then 3


def test_the_order_does_not_depend_on_input_order() -> None:
    base = [cand(k, r) for k, r in (("a", 1), ("b", 2), ("c", 3), ("d", 4))]
    scores = {"a": 0.5, "b": 0.5, "c": 2.0, "d": -1.0}
    expected = None
    for permutation in itertools.permutations(base):
        ranked = order_by_score(
            list(permutation), [scores[c.key] for c in permutation], [False] * 4, top_k=4
        )
        keys = [r.key for r in ranked]
        expected = expected or keys
        assert keys == expected


def test_random_scores_always_give_descending_order() -> None:
    rng = random.Random(3)  # noqa: S311
    for _ in range(40):
        n = rng.randint(1, 30)
        candidates = [cand(f"k{i}", i + 1) for i in range(n)]
        scores = [rng.choice([-2.0, 0.0, 1.5, rng.random()]) for _ in range(n)]

        ranked = order_by_score(candidates, scores, [False] * n, top_k=n)

        assert [r.score for r in ranked] == sorted(scores, reverse=True)
        assert sorted(r.key for r in ranked) == sorted(c.key for c in candidates)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_score_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        order_by_score([cand("a", 1), cand("b", 2)], [1.0, bad], [False, False], top_k=2)


def test_a_missing_score_or_flag_is_refused() -> None:
    with pytest.raises(ValueError, match="one score"):
        order_by_score([cand("a", 1), cand("b", 2)], [1.0], [False, False], top_k=2)
    with pytest.raises(ValueError, match="one score"):
        order_by_score([cand("a", 1), cand("b", 2)], [1.0, 2.0], [False], top_k=2)


def test_duplicates_keep_the_best_ranked_occurrence() -> None:
    kept = unique_candidates([cand("a", 1, "first"), cand("b", 2), cand("a", 3, "second")])

    assert [(c.key, c.retrieval_rank, c.text) for c in kept] == [("a", 1, "first"), ("b", 2, "t")]


def test_empty_input_gives_empty_output() -> None:
    assert unique_candidates([]) == []
    assert order_by_score([], [], [], top_k=5) == []


def test_uuid_keys_work() -> None:
    ids = [uuid.UUID(int=n) for n in (3, 1, 2)]
    candidates = [RerankInput(k, "t", 1) for k in ids]

    ranked = order_by_score(candidates, [0.0] * 3, [False] * 3, top_k=3)

    assert [r.key for r in ranked] == sorted(ids, key=str)
