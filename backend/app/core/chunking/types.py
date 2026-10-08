from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

# Bump when the algorithm's output could change for the same input and configuration.
CHUNKING_VERSION = "c1.0"


class ParagraphMode(StrEnum):
    BLANK_LINE = "blank_line"  # paragraphs are separated by blank lines (PDF, TXT, Markdown)
    LINE = "line"  # every line is its own paragraph (DOCX)


class SizeMeasure(Protocol):
    """Measures a span of `text`. Sizes and overlaps are expressed in this unit.

    The default counts characters. A token-aware measure can be supplied later without
    touching the chunking logic, provided it grows monotonically as the span grows.
    """

    unit: str

    def size(self, text: str, start: int, end: int) -> int: ...


class CharMeasure:
    unit = "chars"

    def size(self, text: str, start: int, end: int) -> int:
        return end - start


@dataclass(frozen=True)
class ChunkingConfig:
    max_size: int
    overlap: int
    min_size: int
    max_chunks: int

    def __post_init__(self) -> None:
        if self.max_size < 1 or self.min_size < 1 or self.overlap < 0 or self.max_chunks < 1:
            raise ValueError("chunking sizes must be positive (overlap may be zero)")
        if self.overlap * 2 > self.max_size:
            raise ValueError("overlap must be at most half of max_size")
        if self.min_size >= self.max_size:
            raise ValueError("min_size must be smaller than max_size")

    def snapshot(self, unit: str = "chars") -> dict[str, Any]:
        return {
            "version": CHUNKING_VERSION,
            "unit": unit,
            "max_size": self.max_size,
            "overlap": self.overlap,
            "min_size": self.min_size,
        }


@dataclass(frozen=True)
class SectionText:
    """One persisted section, as the chunker sees it."""

    ordinal: int
    text: str
    heading: str | None = None
    heading_level: int | None = None
    page_number: int | None = None
    paragraph_mode: ParagraphMode = ParagraphMode.BLANK_LINE
    markdown: bool = False


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before persistence. `text` is exactly `section.text[start_char:end_char]`."""

    chunk_index: int
    section_ordinal: int
    text: str
    start_char: int
    end_char: int
    overlap_chars: int
    page_number: int | None
    heading: str | None
    heading_level: int | None
    heading_path: tuple[str, ...]


@dataclass
class ChunkingResult:
    chunks: list[ChunkDraft]
    skipped_sections: int = 0
    dropped_chunks: int = 0


class Chunker(Protocol):
    version: str

    def chunk(
        self, sections: list[SectionText], deadline_check: "DeadlineCheck"
    ) -> ChunkingResult: ...


class DeadlineCheck(Protocol):
    def check(self) -> None: ...
