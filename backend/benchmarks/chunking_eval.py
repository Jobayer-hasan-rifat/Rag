"""Chunking quality evaluation on a deterministic synthetic corpus.

Run from `backend/`:  python -m benchmarks.chunking_eval

Every metric is computed from real chunker output; nothing is estimated. The corpus is generated
from a seeded random choice of sentences (no real documents), so results are reproducible and
paragraph/sentence lengths vary enough to exercise packing, splitting and overlap.
`tests/unit/test_chunking_evaluation.py` asserts thresholds on the same metrics so a regression
in chunk quality fails the build.
"""

import random
import statistics
import sys
import unicodedata
from dataclasses import dataclass

from app.core.chunking.chunker import StructureAwareChunker
from app.core.chunking.types import (
    ChunkDraft,
    ChunkingConfig,
    ChunkingResult,
    ParagraphMode,
    SectionText,
)

DEFAULT_CONFIG = ChunkingConfig(max_size=1000, overlap=150, min_size=20, max_chunks=1_000_000)
SEED = 20261009

ENGLISH_SENTENCES = [
    "The retrieval system splits documents into passages.",
    "Each passage keeps its page number and heading.",
    "Short sentences help.",
    "Longer sentences, with several clauses and a few commas, are also common in real "
    "reports; they must not be cut in the middle.",
    "Revenue grew by 12.5% in Q3 (see Table 2), driven mainly by the “enterprise” segment.",
    "Dr. Rahman presented the results at 10 a.m. on Monday.",
    "Was the baseline measured on the same hardware? It was.",
    "Version 2.3.1 of the library fixed three bugs.",
    "In 2023, the committee approved the budget of $1,200,000 for the next fiscal year.",
    "Unusually long identifiers such as config_parser_default_timeout_seconds appear in "
    "technical text.",
    "Wait… that changes the plan.",
    "The appendix lists every term used in this report and its definition in full detail.",
]
BANGLA_SENTENCES = [
    "বাংলাদেশের রাজধানী ঢাকা।",
    "এটি একটি পরীক্ষামূলক বাক্য যা বারবার ব্যবহৃত হয়।",
    "ক্ষুদ্র বাক্য।",
    "ক্ষুদ্র ও বড় বাক্য উভয়ই থাকে, এবং কখনও কখনও কমা ও যুক্তাক্ষর ব্যবহৃত হয়, "
    "যেমন বিজ্ঞান, শিক্ষা ও প্রযুক্তি সম্পর্কিত লেখায়।",
    "২০২৩ সালে প্রকল্পের মোট ব্যয় হয়েছিল ১২,০০০ টাকা।",
    "তিনি কি আজ আসবেন? হ্যাঁ, আসবেন।",
    "প্রতিবেদনের শেষে সব পরিভাষার একটি তালিকা দেওয়া হয়েছে এবং প্রতিটি শব্দের সংজ্ঞা "
    "বিস্তারিত লেখা আছে।",
]
MIXED_SENTENCES = ENGLISH_SENTENCES + BANGLA_SENTENCES
TERMINATORS = (".", "!", "?", "।", "॥", "…")
_NOT_A_START = {"‌", "‍"}


class _NoDeadline:
    def check(self) -> None:
        return None


@dataclass(frozen=True)
class CorpusCase:
    name: str
    sections: list[SectionText]
    prose: bool  # sentence/paragraph boundary quality only applies to prose


def _paragraphs(bank: list[str], paragraphs: int, *, seed: int, max_sentences: int = 9) -> str:
    rng = random.Random(seed)
    return "\n\n".join(
        " ".join(rng.choice(bank) for _ in range(rng.randint(1, max_sentences)))
        for _ in range(paragraphs)
    )


def _markdown_corpus() -> str:
    return "".join(
        f"## Heading {i}\n\n{_paragraphs(ENGLISH_SENTENCES, 2, seed=i)}\n\n- item one\n"
        f"- item two\n\n```\ncode line {i}\n\nmore code\n```\n\n"
        f"| a | b |\n|---|---|\n| {i} | {i + 1} |\n\n"
        for i in range(60)
    )


def build_corpus() -> list[CorpusCase]:
    rng = random.Random(SEED)
    one_paragraph = " ".join(rng.choice(ENGLISH_SENTENCES) for _ in range(1800))
    docx_style = _paragraphs(MIXED_SENTENCES, 300, seed=4, max_sentences=6).replace("\n\n", "\n")
    return [
        CorpusCase(
            "English prose", [SectionText(0, _paragraphs(ENGLISH_SENTENCES, 120, seed=1))], True
        ),
        CorpusCase(
            "Bangla prose", [SectionText(0, _paragraphs(BANGLA_SENTENCES, 120, seed=2))], True
        ),
        CorpusCase(
            "Mixed Bangla/English",
            [SectionText(0, _paragraphs(MIXED_SENTENCES, 120, seed=3))],
            True,
        ),
        CorpusCase(
            "Paged document (50 pages)",
            [
                SectionText(i, _paragraphs(MIXED_SENTENCES, 5, seed=100 + i), page_number=i + 1)
                for i in range(50)
            ],
            True,
        ),
        CorpusCase(
            "Headed sections",
            [
                SectionText(
                    i,
                    _paragraphs(ENGLISH_SENTENCES, 3, seed=200 + i),
                    heading=f"H{i}",
                    heading_level=1 + i % 3,
                )
                for i in range(60)
            ],
            True,
        ),
        CorpusCase(
            "Line paragraphs (DOCX style)",
            [SectionText(0, docx_style, paragraph_mode=ParagraphMode.LINE)],
            True,
        ),
        CorpusCase(
            "Markdown with code and tables",
            [SectionText(0, _markdown_corpus(), markdown=True)],
            False,
        ),
        CorpusCase("One 200k-character paragraph", [SectionText(0, one_paragraph)], True),
        CorpusCase("No whitespace (hard cuts)", [SectionText(0, "x" * 100_000)], False),
        CorpusCase("Bangla conjuncts, no spaces", [SectionText(0, "ক্ষ" * 20_000 + "‍")], False),
    ]


@dataclass
class CaseMetrics:
    name: str
    chunks: int
    source_chars: int
    mean_size: float
    p10_size: int
    p90_size: int
    max_size: int
    size_compliance: float  # share of chunks within the size limit
    slice_integrity: float  # share of chunks equal to their section slice
    coverage: float  # share of non-space source characters inside some chunk
    boundary_quality: float  # share of chunks that end on a sentence or paragraph boundary
    start_quality: float  # share of chunks that start at a section, paragraph or word start
    grapheme_violations: int  # chunks starting on a mark/joiner or ending on a virama
    overlap_ratio: float  # overlapped characters / chunk characters
    fragment_ratio: float  # share of chunks shorter than min_size
    deterministic: bool


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _ends_on_boundary(chunk: ChunkDraft, section: SectionText) -> bool:
    text = section.text
    stripped = chunk.text.rstrip("\"')]”’»")
    if stripped.endswith(TERMINATORS):
        return True
    return chunk.end_char >= len(text) or text[chunk.end_char : chunk.end_char + 2].startswith("\n")


def _starts_on_boundary(chunk: ChunkDraft, section: SectionText) -> bool:
    return chunk.start_char == 0 or section.text[chunk.start_char - 1].isspace()


def _grapheme_violation(chunk: ChunkDraft) -> bool:
    first, last = chunk.text[0], chunk.text[-1]
    return (
        unicodedata.category(first).startswith("M")
        or first in _NOT_A_START
        or unicodedata.combining(last) == 9
    )


def _coverage(sections: list[SectionText], result: ChunkingResult) -> float:
    total = covered = 0
    spans: dict[int, list[tuple[int, int]]] = {}
    for chunk in result.chunks:
        spans.setdefault(chunk.section_ordinal, []).append((chunk.start_char, chunk.end_char))
    for section in sections:
        marked = bytearray(len(section.text))
        for start, end in spans.get(section.ordinal, []):
            marked[start:end] = b"\x01" * (end - start)
        for index, char in enumerate(section.text):
            if not char.isspace():
                total += 1
                covered += marked[index]
    return covered / total if total else 1.0


def evaluate_case(case: CorpusCase, config: ChunkingConfig = DEFAULT_CONFIG) -> CaseMetrics:
    chunker = StructureAwareChunker(config)
    result = chunker.chunk(case.sections, _NoDeadline())
    again = chunker.chunk(case.sections, _NoDeadline())
    by_ordinal = {s.ordinal: s for s in case.sections}
    chunks = result.chunks
    sizes = [len(c.text) for c in chunks]
    ends = [_ends_on_boundary(c, by_ordinal[c.section_ordinal]) for c in chunks]
    starts = [_starts_on_boundary(c, by_ordinal[c.section_ordinal]) for c in chunks]
    exact = [
        c.text == by_ordinal[c.section_ordinal].text[c.start_char : c.end_char] for c in chunks
    ]
    return CaseMetrics(
        name=case.name,
        chunks=len(chunks),
        source_chars=sum(len(s.text) for s in case.sections),
        mean_size=statistics.fmean(sizes),
        p10_size=_percentile(sizes, 0.10),
        p90_size=_percentile(sizes, 0.90),
        max_size=max(sizes),
        size_compliance=sum(s <= config.max_size for s in sizes) / len(sizes),
        slice_integrity=sum(exact) / len(chunks),
        coverage=_coverage(case.sections, result),
        boundary_quality=sum(ends) / len(ends),
        start_quality=sum(starts) / len(starts),
        grapheme_violations=sum(_grapheme_violation(c) for c in chunks),
        overlap_ratio=sum(c.overlap_chars for c in chunks) / sum(sizes),
        fragment_ratio=sum(s < config.min_size for s in sizes) / len(sizes),
        deterministic=chunks == again.chunks,
    )


def evaluate_all(config: ChunkingConfig = DEFAULT_CONFIG) -> list[tuple[CorpusCase, CaseMetrics]]:
    return [(case, evaluate_case(case, config)) for case in build_corpus()]


def main() -> None:
    print(f"Chunking evaluation, config {DEFAULT_CONFIG.snapshot()}\n")
    columns = [
        "Case", "Chunks", "Mean", "P10", "P90", "Max", "Within limit", "Exact slices",
        "Coverage", "Ends on boundary", "Starts on boundary", "Grapheme faults",
        "Overlap", "Fragments", "Deterministic",
    ]  # fmt: skip
    print("| " + " | ".join(columns) + " |")
    print("|" + "---|" * len(columns))
    for case, m in evaluate_all():
        cells = [
            m.name, f"{m.chunks:,}", round(m.mean_size), m.p10_size, m.p90_size, m.max_size,
            f"{m.size_compliance:.1%}", f"{m.slice_integrity:.1%}", f"{m.coverage:.1%}",
            f"{m.boundary_quality:.1%}" if case.prose else "n/a",
            f"{m.start_quality:.1%}" if case.prose else "n/a",
            m.grapheme_violations, f"{m.overlap_ratio:.1%}", f"{m.fragment_ratio:.1%}",
            "yes" if m.deterministic else "NO",
        ]  # fmt: skip
        print("| " + " | ".join(str(cell) for cell in cells) + " |")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
