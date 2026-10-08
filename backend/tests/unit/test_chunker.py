import hashlib

import pytest

from app.core.chunking.chunker import StructureAwareChunker
from app.core.chunking.hierarchy import HeadingTracker
from app.core.chunking.segmentation import safe_cut, segment_blocks, sentence_spans
from app.core.chunking.types import (
    CHUNKING_VERSION,
    ChunkingConfig,
    ChunkingResult,
    ParagraphMode,
    SectionText,
)
from app.core.documents.processing import FailureReason, ProcessingFailure


class NoDeadline:
    def check(self) -> None:
        return None


def make(
    max_size: int = 200, overlap: int = 40, min_size: int = 30, max_chunks: int = 1000
) -> StructureAwareChunker:
    return StructureAwareChunker(ChunkingConfig(max_size, overlap, min_size, max_chunks))


def run(sections: list[SectionText], **kwargs: int) -> ChunkingResult:
    return make(**kwargs).chunk(sections, NoDeadline())


def assert_exact_slices(sections: list[SectionText], result: ChunkingResult) -> None:
    by_ordinal = {s.ordinal: s for s in sections}
    for chunk in result.chunks:
        section = by_ordinal[chunk.section_ordinal]
        assert chunk.text == section.text[chunk.start_char : chunk.end_char]
        assert chunk.text.strip() == chunk.text
        assert 0 <= chunk.overlap_chars <= len(chunk.text)


def paragraphs(count: int, words: int = 12) -> str:
    return "\n\n".join(" ".join(f"word{p}x{w}" for w in range(words)) + "." for p in range(count))


# --- configuration -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [(0, 0, 1, 1), (100, 60, 10, 5), (100, 10, 100, 5), (100, -1, 10, 5), (100, 10, 10, 0)],
)
def test_invalid_chunking_configuration_is_rejected(args: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(*args)


def test_config_snapshot_records_version_and_sizes() -> None:
    snapshot = ChunkingConfig(100, 10, 20, 5).snapshot()
    assert snapshot == {
        "version": CHUNKING_VERSION,
        "unit": "chars",
        "max_size": 100,
        "overlap": 10,
        "min_size": 20,
    }


# --- basics --------------------------------------------------------------------------------


def test_empty_input_produces_no_chunks() -> None:
    assert run([]).chunks == []
    assert run([SectionText(0, "")]).chunks == []
    assert run([SectionText(0, "   \n\n  ")]).chunks == []


def test_short_section_becomes_one_chunk_with_metadata() -> None:
    section = SectionText(3, "A short paragraph of text.", page_number=7)
    result = run([section])
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk.text == "A short paragraph of text."
    assert (chunk.chunk_index, chunk.section_ordinal, chunk.page_number) == (0, 3, 7)
    assert chunk.overlap_chars == 0
    assert hashlib.sha256(chunk.text.encode()).hexdigest()


def test_chunks_are_indexed_consecutively_across_sections() -> None:
    sections = [SectionText(i, paragraphs(6), page_number=i + 1) for i in range(3)]
    result = run(sections)
    assert [c.chunk_index for c in result.chunks] == list(range(len(result.chunks)))
    assert_exact_slices(sections, result)


def test_chunks_never_span_sections() -> None:
    sections = [SectionText(0, "First section text here."), SectionText(1, "Second section.")]
    result = run(sections, min_size=5)
    assert [c.section_ordinal for c in result.chunks] == [0, 1]


def test_chunks_respect_the_size_limit() -> None:
    sections = [SectionText(0, paragraphs(40))]
    result = run(sections, max_size=150, overlap=20)
    assert len(result.chunks) > 3
    assert all(len(c.text) <= 150 for c in result.chunks)
    assert_exact_slices(sections, result)


def test_paragraph_boundaries_are_preferred_split_points() -> None:
    text = paragraphs(10, words=6)
    result = run([SectionText(0, text)], max_size=120, overlap=0)
    for chunk in result.chunks:
        assert chunk.text.endswith(".")
        assert chunk.text.startswith("word")


def test_small_paragraphs_are_packed_together() -> None:
    result = run([SectionText(0, paragraphs(4, words=3))], max_size=500, overlap=0)
    assert len(result.chunks) == 1
    assert result.chunks[0].text.count("\n\n") == 3


# --- structure -------------------------------------------------------------------------------


def test_heading_metadata_is_carried_onto_chunks() -> None:
    section = SectionText(0, paragraphs(2), heading="Intro", heading_level=1)
    chunk = run([section]).chunks[0]
    assert (chunk.heading, chunk.heading_level) == ("Intro", 1)
    assert chunk.heading_path == ("Intro",)


def test_heading_path_nests_and_resets_by_level() -> None:
    body = "Some body text for this section."
    sections = [
        SectionText(0, body, heading="A", heading_level=1),
        SectionText(1, body, heading="A.1", heading_level=2),
        SectionText(2, body, heading="A.1.x", heading_level=3),
        SectionText(3, body, heading="A.2", heading_level=2),
        SectionText(4, body, heading="B", heading_level=1),
        SectionText(5, body),
    ]
    paths = [c.heading_path for c in run(sections, min_size=5).chunks]
    assert paths == [
        ("A",),
        ("A", "A.1"),
        ("A", "A.1", "A.1.x"),
        ("A", "A.2"),
        ("B",),
        ("B",),
    ]


def test_heading_only_section_yields_no_chunk_but_keeps_path_for_later_sections() -> None:
    sections = [
        SectionText(0, "", heading="Chapter", heading_level=1),
        SectionText(1, "Body of the chapter, long enough to keep."),
    ]
    result = run(sections, min_size=5)
    assert len(result.chunks) == 1
    assert result.chunks[0].heading_path == ("Chapter",)


def test_heading_tracker_without_heading_returns_current_path() -> None:
    tracker = HeadingTracker()
    assert tracker.path_for(None, None) == ()
    assert tracker.path_for("Top", 1) == ("Top",)
    assert tracker.path_for(None, None) == ("Top",)
    assert tracker.path_for("Sub", 5) == ("Top", "Sub")


def test_markdown_code_fence_is_not_split_by_blank_lines() -> None:
    text = "Intro paragraph.\n\n```\nline one\n\nline two\n```\n\nOutro paragraph."
    blocks = segment_blocks(text, ParagraphMode.BLANK_LINE, markdown=True)
    code = [b for b in blocks if b.kind == "code"]
    assert len(code) == 1
    assert "line one\n\nline two" in text[code[0].start : code[0].end]


def test_table_rows_form_one_block() -> None:
    text = "Before.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter."
    blocks = segment_blocks(text, ParagraphMode.BLANK_LINE, markdown=True)
    kinds = [b.kind for b in blocks]
    assert kinds.count("table") == 1


def test_line_mode_treats_each_line_as_a_paragraph() -> None:
    text = "first line\nsecond line\nthird line"
    blocks = segment_blocks(text, ParagraphMode.LINE, markdown=False)
    assert [text[b.start : b.end] for b in blocks] == ["first line", "second line", "third line"]


def test_line_mode_chunks_pack_whole_lines() -> None:
    text = "\n".join(f"Line number {i} with a few words." for i in range(30))
    section = SectionText(0, text, paragraph_mode=ParagraphMode.LINE)
    result = run([section], max_size=120, overlap=0)
    assert all(c.text.startswith("Line number") for c in result.chunks)
    assert_exact_slices([section], result)


# --- oversized blocks and overlap -------------------------------------------------------------


def test_oversized_paragraph_splits_on_sentences_with_overlap() -> None:
    sentences = [f"This is sentence number {i} of the long paragraph." for i in range(20)]
    section = SectionText(0, " ".join(sentences))
    result = run([section], max_size=160, overlap=50)
    assert len(result.chunks) > 3
    assert all(len(c.text) <= 160 for c in result.chunks)
    assert any(c.overlap_chars > 0 for c in result.chunks[1:])
    assert result.chunks[0].overlap_chars == 0
    assert_exact_slices([section], result)


def test_overlap_text_is_shared_between_neighbouring_chunks() -> None:
    sentences = [f"Sentence {i} says something quite specific." for i in range(15)]
    section = SectionText(0, " ".join(sentences))
    result = run([section], max_size=150, overlap=60)
    for previous, current in zip(result.chunks, result.chunks[1:], strict=False):
        if current.overlap_chars:
            assert previous.end_char > current.start_char
            shared = section.text[current.start_char : previous.end_char]
            assert len(shared) <= current.overlap_chars + 1


def test_no_overlap_between_separate_paragraph_chunks() -> None:
    result = run([SectionText(0, paragraphs(12))], max_size=120, overlap=30)
    assert all(c.overlap_chars == 0 for c in result.chunks)


def test_zero_overlap_configuration_never_overlaps() -> None:
    sentences = " ".join(f"Sentence {i} is here and complete." for i in range(30))
    result = run([SectionText(0, sentences)], max_size=100, overlap=0)
    assert all(c.overlap_chars == 0 for c in result.chunks)
    ends = [c.end_char for c in result.chunks]
    starts = [c.start_char for c in result.chunks]
    assert all(s >= e for e, s in zip(ends, starts[1:], strict=False))


def test_text_without_spaces_is_hard_cut_to_the_limit() -> None:
    section = SectionText(0, "x" * 1000)
    result = run([section], max_size=100, overlap=10)
    assert all(len(c.text) <= 100 for c in result.chunks)
    assert "".join(c.text for c in result.chunks if c.overlap_chars == 0)
    assert_exact_slices([section], result)


def test_tiny_trailing_span_is_merged_when_it_fits() -> None:
    from app.core.chunking.chunker import _Span

    chunker = make(max_size=100, overlap=0, min_size=20)
    text = "x" * 110
    assert chunker._merge_small_tail(text, [_Span(0, 60, 0), _Span(60, 70, 0)]) == [_Span(0, 70, 0)]
    # too big to merge, or not small: left alone
    assert len(chunker._merge_small_tail(text, [_Span(0, 95, 0), _Span(95, 105, 0)])) == 2
    assert len(chunker._merge_small_tail(text, [_Span(0, 30, 0), _Span(30, 60, 0)])) == 2
    assert len(chunker._merge_small_tail(text, [_Span(0, 60, 0)])) == 1


def test_chunks_without_letters_or_digits_are_dropped() -> None:
    section = SectionText(0, "Real content in this paragraph.\n\n-----\n\n*** ***")
    result = run([section], min_size=5, max_size=40, overlap=0)
    assert result.dropped_chunks >= 0
    assert all(any(ch.isalnum() for ch in c.text) for c in result.chunks)


def test_numeric_only_chunks_are_kept() -> None:
    result = run([SectionText(0, "2023 2024 2025 1,200.50")], min_size=5)
    assert [c.text for c in result.chunks] == ["2023 2024 2025 1,200.50"]


# --- Bangla and mixed scripts ------------------------------------------------------------------

BANGLA = "আমার সোনার বাংলা।"


def test_bangla_danda_splits_sentences() -> None:
    text = f"{BANGLA} {BANGLA} {BANGLA}"
    spans = sentence_spans(text, 0, len(text))
    assert len(spans) == 3
    assert all(text[s:e].endswith("।") for s, e in spans)


def test_bangla_text_is_chunked_within_limits_and_exactly_sliced() -> None:
    section = SectionText(0, " ".join([BANGLA] * 80))
    result = run([section], max_size=120, overlap=20)
    assert len(result.chunks) > 3
    assert all(len(c.text) <= 120 for c in result.chunks)
    assert_exact_slices([section], result)


def test_mixed_bangla_and_english_survives_chunking() -> None:
    text = " ".join(f"Report {i}: {BANGLA} Done." for i in range(40))
    section = SectionText(0, text)
    result = run([section], max_size=100, overlap=20)
    assert_exact_slices([section], result)
    joined = " ".join(c.text for c in result.chunks)
    assert "বাংলা" in joined and "Report" in joined


def test_hard_cut_never_splits_a_bangla_conjunct() -> None:
    zwj = "‍"
    conjunct = "ক্" + "ষ"  # ক্ষ
    text = (conjunct * 300) + zwj + "র" * 5
    for cut in range(20, len(text) - 1, 7):
        position = safe_cut(text, 0, cut)
        assert 0 < position <= cut
        before, after = text[position - 1], text[position]
        assert after not in "্‍‌" and before != "্"


def test_hard_cut_keeps_combining_marks_with_their_base() -> None:
    text = "xxxxxéyyyy"
    position = safe_cut(text, 0, 6)
    assert position == 5
    assert text[position] == "e"


def test_bangla_without_spaces_is_hard_cut_without_breaking_clusters() -> None:
    text = "ক্ষ" * 400
    section = SectionText(0, text)
    result = run([section], max_size=50, overlap=0)
    for chunk in result.chunks:
        assert not chunk.text.startswith("্") and not chunk.text.endswith("্")
    assert_exact_slices([section], result)


# --- determinism, limits, deadline -----------------------------------------------------------


def test_chunking_is_deterministic() -> None:
    sections = [
        SectionText(
            0, paragraphs(25) + " " + " ".join([BANGLA] * 30), heading="H", heading_level=1
        ),
        SectionText(1, "x" * 700),
    ]
    first = run(sections, max_size=140, overlap=30)
    second = run(sections, max_size=140, overlap=30)
    assert first.chunks == second.chunks


def test_changing_the_configuration_changes_the_chunking() -> None:
    sections = [SectionText(0, paragraphs(30))]
    assert run(sections, max_size=150).chunks != run(sections, max_size=300).chunks


def test_too_many_chunks_fails_with_a_safe_reason() -> None:
    sections = [SectionText(0, paragraphs(200))]
    with pytest.raises(ProcessingFailure) as raised:
        run(sections, max_size=100, overlap=0, max_chunks=5)
    assert raised.value.reason is FailureReason.TOO_MANY_CHUNKS


def test_deadline_is_checked_while_chunking() -> None:
    class Expired:
        def check(self) -> None:
            raise ProcessingFailure(FailureReason.TIMEOUT)

    with pytest.raises(ProcessingFailure) as raised:
        make().chunk([SectionText(0, paragraphs(20))], Expired())
    assert raised.value.reason is FailureReason.TIMEOUT


def test_pathological_inputs_finish_quickly_and_within_limits() -> None:
    cases = [
        "a" * 200_000,
        ("word " * 50_000),
        "\n" * 100_000 + "end",
        "." * 100_000,
        "। " * 30_000,
        ("| a | b |\n" * 20_000),
        "```\n" + "code line\n" * 20_000,
    ]
    for text in cases:
        section = SectionText(0, text)
        result = run([section], max_size=500, overlap=50, max_chunks=100_000)
        assert all(len(c.text) <= 500 for c in result.chunks)
        assert_exact_slices([section], result)


def test_custom_size_measure_is_used_for_limits() -> None:
    class WordMeasure:
        unit = "words"

        def size(self, text: str, start: int, end: int) -> int:
            return len(text[start:end].split())

    chunker = StructureAwareChunker(ChunkingConfig(10, 2, 3, 1000), measure=WordMeasure())
    text = " ".join(f"w{i}" for i in range(100)) + "."
    result = chunker.chunk([SectionText(0, text)], NoDeadline())
    assert result.chunks
    assert all(len(c.text.split()) <= 10 for c in result.chunks)


@pytest.mark.parametrize(
    "text",
    [
        "." * 300_000,
        "." + '"' * 300_000,
        "a" + "\u200d" * 300_000,
        "\u0995" + "\u09cd" * 300_000,
        "e" + "\u0301" * 300_000,
        "ক্ষ" * 100_000,
    ],
    ids=["dots", "dot-quotes", "zwj-run", "virama-run", "marks-run", "conjuncts"],
)
def test_pathological_runs_are_chunked_in_linear_time(text: str) -> None:
    import time

    section = SectionText(0, text)
    started = time.perf_counter()
    result = run([section], max_size=1000, overlap=150)
    elapsed = time.perf_counter() - started

    assert elapsed < 10  # these took 40-110 s while the scans were quadratic
    assert all(len(c.text) <= 1000 for c in result.chunks)
    assert_exact_slices([section], result)


def test_hard_cut_gives_up_on_a_run_of_marks_instead_of_scanning_the_whole_chunk() -> None:
    text = "a" + "\u0301" * 5000
    assert safe_cut(text, 0, 1000) == 1000  # no safe point within the scan window
    assert safe_cut("x" * 10 + "e\u0301" + "y" * 10, 0, 11) == 10  # a real cluster still works
