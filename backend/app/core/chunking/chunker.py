from dataclasses import dataclass

from app.core.chunking.hierarchy import HeadingTracker
from app.core.chunking.segmentation import (
    Block,
    line_spans,
    safe_cut,
    segment_blocks,
    sentence_spans,
    word_spans,
)
from app.core.chunking.types import (
    CHUNKING_VERSION,
    CharMeasure,
    ChunkDraft,
    ChunkingConfig,
    ChunkingResult,
    DeadlineCheck,
    SectionText,
    SizeMeasure,
)
from app.core.documents.processing import FailureReason, ProcessingFailure

_DEADLINE_EVERY = 256
_STRUCTURED_LEVELS = ("lines", "sentences", "words")
_PROSE_LEVELS = ("sentences", "words")
_SPLITTERS = {"lines": line_spans, "sentences": sentence_spans, "words": word_spans}


@dataclass(frozen=True)
class _Atom:
    start: int
    end: int
    block: int  # index of the originating block; overlap never crosses blocks


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    overlap: int


class StructureAwareChunker:
    """Deterministic, structure-aware chunker.

    Chunks never cross a section (page or heading) boundary, so every chunk keeps an exact
    source location. Within a section the text is segmented into paragraph, table and code
    blocks; whole blocks are packed greedily up to `max_size`. A block that is too large is
    split at progressively finer boundaries (table/code lines, sentences, words) and only as a
    last resort at a hard, grapheme-safe cut. Overlap is applied only between consecutive
    chunks of one oversized block, never across natural paragraph boundaries.
    """

    version = CHUNKING_VERSION

    def __init__(self, config: ChunkingConfig, measure: SizeMeasure | None = None) -> None:
        self.config = config
        self._measure: SizeMeasure = measure or CharMeasure()

    def chunk(self, sections: list[SectionText], deadline_check: DeadlineCheck) -> ChunkingResult:
        result = ChunkingResult(chunks=[])
        tracker = HeadingTracker()
        for section in sections:
            deadline_check.check()
            path = tracker.path_for(section.heading, section.heading_level)
            if not section.text.strip():
                result.skipped_sections += 1
                continue
            for span in self._chunk_section(section, deadline_check):
                text = section.text[span.start : span.end]
                if not any(char.isalnum() for char in text):
                    result.dropped_chunks += 1  # punctuation or symbols only
                    continue
                if len(result.chunks) >= self.config.max_chunks:
                    raise ProcessingFailure(
                        FailureReason.TOO_MANY_CHUNKS, detail=f">{self.config.max_chunks}"
                    )
                result.chunks.append(
                    ChunkDraft(
                        chunk_index=len(result.chunks),
                        section_ordinal=section.ordinal,
                        text=text,
                        start_char=span.start,
                        end_char=span.end,
                        overlap_chars=span.overlap,
                        page_number=section.page_number,
                        heading=section.heading,
                        heading_level=section.heading_level,
                        heading_path=path,
                    )
                )
        return result

    # --- one section ---------------------------------------------------------------------

    def _chunk_section(self, section: SectionText, deadline_check: DeadlineCheck) -> list[_Span]:
        text = section.text
        atoms: list[_Atom] = []
        blocks = segment_blocks(text, section.paragraph_mode, section.markdown)
        for index, block in enumerate(blocks):
            if index % _DEADLINE_EVERY == 0:
                deadline_check.check()
            atoms.extend(_Atom(s, e, index) for s, e in self._atomise(text, block))
        return self._merge_small_tail(text, self._pack(text, atoms))

    def _size(self, text: str, start: int, end: int) -> int:
        return self._measure.size(text, start, end)

    def _atomise(self, text: str, block: Block) -> list[tuple[int, int]]:
        levels = _PROSE_LEVELS if block.kind == "para" else _STRUCTURED_LEVELS
        return self._split(text, block.start, block.end, levels)

    def _split(
        self, text: str, start: int, end: int, levels: tuple[str, ...]
    ) -> list[tuple[int, int]]:
        if self._size(text, start, end) <= self.config.max_size:
            return [(start, end)]
        if not levels:
            return self._hard_cut(text, start, end)
        spans = _SPLITTERS[levels[0]](text, start, end)
        if len(spans) <= 1:
            return self._split(text, start, end, levels[1:])
        atoms: list[tuple[int, int]] = []
        for span_start, span_end in spans:
            atoms.extend(self._split(text, span_start, span_end, levels[1:]))
        return atoms

    def _hard_cut(self, text: str, start: int, end: int) -> list[tuple[int, int]]:
        pieces: list[tuple[int, int]] = []
        position = start
        while position < end:
            limit = self._largest_fit(text, position, end)
            cut = limit if limit >= end else safe_cut(text, position, limit)
            pieces.append((position, cut))
            position = cut
        return pieces

    def _largest_fit(self, text: str, start: int, end: int) -> int:
        """Largest `cut` in (start, end] with size(start, cut) <= max_size (at least start + 1)."""
        low, high = start + 1, end
        while low < high:
            middle = (low + high + 1) // 2
            if self._size(text, start, middle) <= self.config.max_size:
                low = middle
            else:
                high = middle - 1
        return low

    def _pack(self, text: str, atoms: list[_Atom]) -> list[_Span]:
        config = self.config
        spans: list[_Span] = []
        count = len(atoms)
        first = 0
        overlap = 0
        while first < count:
            start = atoms[first].start
            last = first
            while (
                last + 1 < count and self._size(text, start, atoms[last + 1].end) <= config.max_size
            ):
                last += 1
            end = atoms[last].end
            spans.append(_Span(start, end, overlap))
            if last + 1 >= count:
                break
            following = last + 1
            next_first, overlap = following, 0
            if config.overlap and atoms[following].block == atoms[last].block:
                # re-use trailing atoms of this chunk (same block only), always dropping at
                # least the first atom so consecutive chunks never repeat each other
                for candidate in range(first + 1, last + 1):
                    if atoms[candidate].block != atoms[following].block:
                        continue
                    kept = self._size(text, atoms[candidate].start, end)
                    fits = self._size(text, atoms[candidate].start, atoms[following].end)
                    if kept <= config.overlap and fits <= config.max_size:
                        next_first, overlap = candidate, kept
                        break
            first = next_first
        return spans

    def _merge_small_tail(self, text: str, spans: list[_Span]) -> list[_Span]:
        if len(spans) < 2:
            return spans
        last, previous = spans[-1], spans[-2]
        if (
            self._size(text, last.start, last.end) < self.config.min_size
            and self._size(text, previous.start, last.end) <= self.config.max_size
        ):
            return [*spans[:-2], _Span(previous.start, last.end, previous.overlap)]
        return spans
