"""Quality gates for the chunker, measured on the corpus in benchmarks/chunking_eval."""

import pytest

from app.core.chunking.types import ChunkingConfig
from benchmarks.chunking_eval import (
    CaseMetrics,
    CorpusCase,
    build_corpus,
    evaluate_all,
    evaluate_case,
)

RESULTS = evaluate_all()


@pytest.fixture(params=RESULTS, ids=[case.name for case, _ in RESULTS])
def evaluated(request: pytest.FixtureRequest) -> tuple[CorpusCase, CaseMetrics]:
    return request.param  # type: ignore[no-any-return]


def test_every_chunk_respects_the_size_limit_and_is_an_exact_slice(
    evaluated: tuple[CorpusCase, CaseMetrics],
) -> None:
    _, metrics = evaluated
    assert metrics.size_compliance == 1.0
    assert metrics.slice_integrity == 1.0


def test_all_source_text_is_covered_by_some_chunk(
    evaluated: tuple[CorpusCase, CaseMetrics],
) -> None:
    assert evaluated[1].coverage == 1.0


def test_no_chunk_starts_inside_or_ends_inside_a_grapheme_cluster(
    evaluated: tuple[CorpusCase, CaseMetrics],
) -> None:
    assert evaluated[1].grapheme_violations == 0


def test_chunking_is_deterministic_on_the_corpus(evaluated: tuple[CorpusCase, CaseMetrics]) -> None:
    assert evaluated[1].deterministic


def test_prose_chunks_end_on_sentence_or_paragraph_boundaries(
    evaluated: tuple[CorpusCase, CaseMetrics],
) -> None:
    case, metrics = evaluated
    if case.prose:
        assert metrics.boundary_quality >= 0.95
        assert metrics.start_quality >= 0.95


def test_there_are_no_tiny_fragment_chunks(evaluated: tuple[CorpusCase, CaseMetrics]) -> None:
    assert evaluated[1].fragment_ratio <= 0.02


def test_overlap_is_bounded_by_configuration(evaluated: tuple[CorpusCase, CaseMetrics]) -> None:
    config = ChunkingConfig(1000, 150, 20, 1_000_000)
    assert evaluated[1].overlap_ratio <= config.overlap / config.max_size


def test_oversized_paragraph_overlaps_while_paragraph_chunks_do_not() -> None:
    by_name = {case.name: metrics for case, metrics in RESULTS}
    assert by_name["One 200k-character paragraph"].overlap_ratio > 0.05
    assert by_name["English prose"].overlap_ratio == 0.0


def test_chunks_are_reasonably_filled() -> None:
    for case, metrics in RESULTS:
        if case.prose and metrics.chunks > 20:
            assert metrics.mean_size >= 0.55 * 1000, case.name


def test_smaller_configuration_produces_more_chunks_within_its_own_limit() -> None:
    case = next(c for c in build_corpus() if c.name == "Mixed Bangla/English")
    small = evaluate_case(case, ChunkingConfig(400, 60, 20, 1_000_000))
    default = next(m for c, m in RESULTS if c.name == case.name)
    assert small.chunks > default.chunks and small.max_size <= 400
    assert small.size_compliance == 1.0 and small.slice_integrity == 1.0
