"""Context selection: which retrieved chunks the model sees, and under which labels.

Input is the ranked, already authorised retrieval result. This module never loads data, so a
chunk that retrieval did not return can never reach the model. Selection is deterministic: the
order of the input decides, and ties were already broken by retrieval/reranking.

Policies (documented in docs/08):
- duplicates: a chunk whose normalised text equals, is contained in, or overlaps (Jaccard over
  words >= 0.9) one already selected is skipped;
- long chunks: a chunk longer than `max_chunk_tokens` is cut at the last sentence end inside the
  limit (hard cut when there is none) and marked `truncated`: the end of a long chunk is not shown
  to the model, and the citation excerpt shows exactly what was shown;
- budget: blocks are added while the estimated total stays within the budget; a block that does
  not fit is cut to the remaining room if at least `MIN_USEFUL_TOKENS` are left, otherwise
  selection stops;
- the stored chunk text is never modified.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from app.db.repositories.embedding_repository import ChunkRecord
from app.rag.rendering import escape_body, source_open_tag
from app.rag.tokens import estimate_tokens

BLOCK_OVERHEAD_TOKENS = 12  # the closing tag and line breaks; the opening tag is measured
MIN_USEFUL_TOKENS = 80
NEAR_DUPLICATE_JACCARD = 0.9
_SENTENCE_END = re.compile(r"[.!?।॥]\s")
_WORD = re.compile(r"[\wঀ-৿]+", re.UNICODE)


@dataclass(frozen=True)
class ContextBlock:
    label: str  # S1, S2, ... assigned for this request only
    record: ChunkRecord  # the database record: citation metadata comes from here
    text: str  # what the model is shown (possibly cut)
    truncated: bool
    tokens: int
    retrieval_rank: int


@dataclass(frozen=True)
class ContextSelection:
    blocks: list[ContextBlock]
    duplicates_dropped: int
    truncated_blocks: int
    estimated_tokens: int
    budget_tokens: int


def normalise_for_matching(text: str) -> str:
    """NFKC, case-folded, any script's decimal digits as ASCII digits, whitespace collapsed."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    digits = "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() and not ch.isascii() else ch for ch in folded
    )
    return " ".join(digits.split())


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(normalise_for_matching(text)))


def _is_duplicate(text: str, seen: list[tuple[str, frozenset[str]]]) -> bool:
    flat = normalise_for_matching(text)
    words = _words(text)
    for other_flat, other_words in seen:
        if flat == other_flat or (len(flat) >= 40 and flat in other_flat):
            return True
        if len(other_flat) >= 40 and other_flat in flat:
            return True
        union = words | other_words
        if union and len(words & other_words) / len(union) >= NEAR_DUPLICATE_JACCARD:
            return True
    return False


def _rendered_tokens(text: str) -> int:
    return estimate_tokens(escape_body(text))


def cut_to_tokens(
    text: str, max_tokens: int, measure: Callable[[str], int] = estimate_tokens
) -> tuple[str, bool]:
    """The longest prefix whose estimate fits, ending at a sentence end when one exists."""
    if measure(text) <= max_tokens:
        return text, False
    low, high = 0, len(text)
    while low < high:  # largest prefix length that still fits
        middle = (low + high + 1) // 2
        if measure(text[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    prefix = text[:low]
    ends = list(_SENTENCE_END.finditer(prefix))
    if ends and ends[-1].end() >= len(prefix) // 2:
        prefix = prefix[: ends[-1].start() + 1]
    return prefix.rstrip(), True


class ContextBuilder:
    def __init__(self, *, max_sources: int, max_chunk_tokens: int, budget_tokens: int) -> None:
        self._max_sources = max_sources
        self._max_chunk_tokens = max_chunk_tokens
        self._budget = budget_tokens

    def build(self, ranked: list[tuple[ChunkRecord, int]]) -> ContextSelection:
        """`ranked` is (record, retrieval_rank), best first."""
        blocks: list[ContextBlock] = []
        seen: list[tuple[str, frozenset[str]]] = []
        duplicates = truncated_blocks = used = 0
        for record, rank in ranked:
            if len(blocks) >= self._max_sources:
                break
            if not record.text.strip():
                continue
            if _is_duplicate(record.text, seen):
                duplicates += 1
                continue
            label = f"S{len(blocks) + 1}"
            overhead = BLOCK_OVERHEAD_TOKENS + estimate_tokens(source_open_tag(label, record))
            room = self._budget - used - overhead
            limit = min(self._max_chunk_tokens, room)
            if limit < (MIN_USEFUL_TOKENS if blocks else 1):
                break
            text, cut = cut_to_tokens(record.text, limit, _rendered_tokens)
            tokens = _rendered_tokens(text) + overhead
            blocks.append(ContextBlock(f"S{len(blocks) + 1}", record, text, cut, tokens, rank))
            seen.append((normalise_for_matching(record.text), _words(record.text)))
            truncated_blocks += cut
            used += tokens
        return ContextSelection(blocks, duplicates, truncated_blocks, used, self._budget)
