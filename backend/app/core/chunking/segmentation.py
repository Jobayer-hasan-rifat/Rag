"""Boundary detection for chunking: blocks, sentences, words and safe cut points.

Everything here is Unicode-aware and language-agnostic: sentence ends include the Bengali
danda (U+0964/U+0965), no ASCII word-boundary assumptions are made, and a forced cut never
separates a base character from its combining marks, a virama conjunct, or ZWJ/ZWNJ.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.core.chunking.types import ParagraphMode

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# The lookbehind and possessive quantifiers keep the scan linear: a long run of punctuation with
# no following whitespace is read once instead of once per starting position.
_SENTENCE_END = re.compile(
    r"(?<![.!?\u0964\u0965\u2026])[.!?\u0964\u0965\u2026]++[\"')\]\u201d\u2019\u00bb]*+(?=\s)"
)
_WORD = re.compile(r"\S+")
_JOINERS = {"\u200c", "\u200d"}
_VIRAMA_CLASS = 9  # Unicode canonical combining class of Indic viramas (for example U+09CD)
# Real grapheme clusters are a handful of code points. Searching further back would only help
# pathological input (a run of combining marks) and would make hard cuts quadratic on it.
_MAX_CLUSTER_SCAN = 64


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    kind: str  # "para", "table" or "code"


def _is_table_line(stripped: str) -> bool:
    return "|" in stripped and (stripped.count("|") >= 2 or " | " in stripped)


def segment_blocks(text: str, mode: ParagraphMode, markdown: bool) -> list[Block]:
    """Split a section into paragraph, table and code blocks with exact offsets."""
    blocks: list[Block] = []
    current: list[int] = []  # [start, end]
    kind = ""
    fence: str | None = None

    def flush() -> None:
        nonlocal kind
        if current:
            blocks.append(Block(current[0], current[1], kind))
            current.clear()
        kind = ""

    def start(first: int, last: int, new_kind: str) -> None:
        nonlocal kind
        current[:] = [first, last]
        kind = new_kind

    position = 0
    for line in text.split("\n"):
        line_start, line_end = position, position + len(line)
        position = line_end + 1
        stripped = line.strip()

        if markdown:
            fence_match = _FENCE.match(line)
            if fence is not None:
                current[1] = line_end
                if (
                    fence_match
                    and fence_match.group(1)[0] == fence[0]
                    and len(fence_match.group(1)) >= len(fence)
                ):
                    fence = None
                    flush()
                continue
            if fence_match:
                flush()
                fence = fence_match.group(1)
                start(line_start, line_end, "code")
                continue

        if not stripped:
            flush()
        elif _is_table_line(stripped):
            if kind == "table":
                current[1] = line_end
            else:
                flush()
                start(line_start, line_end, "table")
        elif mode is ParagraphMode.LINE:
            flush()
            blocks.append(Block(line_start, line_end, "para"))
        elif kind == "para":
            current[1] = line_end
        else:
            flush()
            start(line_start, line_end, "para")
    flush()

    # a lone pipe-containing line is ordinary text, not a table
    return [
        (
            Block(b.start, b.end, "para")
            if b.kind == "table" and "\n" not in text[b.start : b.end]
            else b
        )
        for b in blocks
    ]


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def line_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = start
    for line in text[start:end].split("\n"):
        trimmed = _trim(text, position, position + len(line))
        if trimmed:
            spans.append(trimmed)
        position += len(line) + 1
    return spans


def sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = start
    for match in _SENTENCE_END.finditer(text, start, end):
        trimmed = _trim(text, position, match.end())
        if trimmed:
            spans.append(trimmed)
        position = match.end()
    tail = _trim(text, position, end)
    if tail:
        spans.append(tail)
    return spans


def word_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _WORD.finditer(text, start, end)]


def safe_cut(text: str, start: int, cut: int) -> int:
    """Move a forced cut left so it does not split a grapheme-like unit.

    A cut is unsafe when the next character is a combining mark or joiner, or when the previous
    character is a virama (the first half of a conjunct such as ক্ষ).
    """
    for position in range(cut, max(start, cut - _MAX_CLUSTER_SCAN), -1):
        if not (_continues(text[position]) or _opens_conjunct(text[position - 1])):
            return position
    return cut  # no safe point nearby (a pathological run of marks): cut at the limit


def _continues(char: str) -> bool:
    return char in _JOINERS or unicodedata.category(char).startswith("M")


def _opens_conjunct(char: str) -> bool:
    return unicodedata.combining(char) == _VIRAMA_CLASS or char in _JOINERS
