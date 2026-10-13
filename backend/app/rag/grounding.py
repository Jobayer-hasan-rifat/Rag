"""Turning a model completion into a validated, cited answer.

Everything here is deterministic and runs locally, whatever the provider promised about structured
output. The model's text is data: its JSON is parsed strictly, its labels are checked against the
sources actually supplied, and sentences that fail a check are removed instead of repaired.

Checks, per sentence:
1. labels: unknown labels (not supplied in this request) are removed; a sentence left with no valid
   label is dropped;
2. quotations: a double-quoted span (4+ characters) must occur, after normalisation, inside one
   cited source's shown text, otherwise the sentence is dropped;
3. literals: numbers and identifiers (anything containing a digit, in any script) must occur as
   whole tokens in a cited source's shown text, otherwise the sentence is dropped.

These checks catch fabricated labels, quotes and figures. They do not prove that a
supported-looking sentence is true: a sentence without digits or quotes is only checked for having
a valid label, a real number can still be attached to a wrong claim, and a correct sentence that
computes or spells out a number may be dropped. The residual risk is documented, not hidden.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.rag.context import ContextBlock, normalise_for_matching

MAX_RAW_CHARS = 100_000
MAX_ANSWER_CHARS = 20_000
_MARKER = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
_TRAILING_MARKERS = re.compile(r"\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]")
_LABEL = re.compile(r"S\d+")
_TERMINATORS = ".!?।॥"
_QUOTE = re.compile(r'"([^"\n]{4,})"|“([^”\n]{4,})”|«([^»\n]{4,})»')
# a token containing a digit, with identifier prefixes (err-4721) and separators (05/03/2024, 3.5)
_LITERAL = re.compile(r"(?:\w+[-_])*\w*\d\w*(?:[-./,:]\w+)*")
_ABBREVIATIONS = frozenset(
    {"vs", "e.g", "i.e", "dr", "mr", "mrs", "ms", "no", "etc", "fig", "approx", "st", "inc", "ltd"}
)


class InvalidModelOutputError(ValueError):
    """The completion is not the JSON object the prompt asked for."""


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answerable: bool | None = None
    answer: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    citations: list[str] = Field(default_factory=list, max_length=50)


@dataclass(frozen=True)
class GroundedAnswer:
    answerable: bool
    text: str
    labels: list[str]  # cited labels in order of first use, all valid
    removed_unknown_labels: int = 0
    removed_uncited_sentences: int = 0
    removed_fabricated_quotes: int = 0
    removed_unsupported_literals: int = 0
    reason: str | None = None  # why the answer is not answerable, when it is not
    granularity: str = "sentence"  # sentence: inline markers; answer: declared labels only


@dataclass
class _Counts:
    unknown: int = 0
    uncited: int = 0
    quotes: int = 0
    literals: int = 0
    kept: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else text[3:]
    text = text.rstrip()
    return text[:-3] if text.endswith("```") else text


def parse_model_output(raw: str) -> ModelOutput:
    """Strictly parse the first JSON object in `raw` (code fences tolerated)."""
    if len(raw) > MAX_RAW_CHARS:
        raise InvalidModelOutputError("completion too long")
    text = _strip_fence(raw)
    start = text.find("{")
    if start < 0:
        raise InvalidModelOutputError("no JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
        return ModelOutput.model_validate(value)
    except (ValueError, ValidationError) as error:
        raise InvalidModelOutputError("not a valid answer object") from error


def _ends_sentence(text: str, start: int, i: int) -> bool:
    """Whether the terminator at `text[i]` ends the sentence that began at `start`."""
    ch = text[i]
    following = text[i + 1] if i + 1 < len(text) else " "
    if not (following.isspace() or following == "[" or following in _TERMINATORS):
        return False
    if ch != ".":
        return True
    previous = text[i - 1] if i > 0 else " "
    if previous.isdigit() and following.isdigit():
        return False
    words = text[start:i].split()
    last = words[-1].lower() if words else ""
    if last in _ABBREVIATIONS:
        return False
    if last.isdigit() and len(words) == 1:  # a list number such as "1."
        return False
    rest = text[i + 1 :].lstrip()
    return not (rest[:1].isalpha() and rest[:1].islower())  # "vs. last year" continues


def split_sentences(text: str) -> list[str]:
    """Sentences with any trailing citation markers attached, in order."""
    sentences: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        end = ch == "\n" or (ch in _TERMINATORS and _ends_sentence(text, start, i))
        if not end:
            i += 1
            continue
        j = i + 1
        while j < len(text) and text[j] in _TERMINATORS:
            j += 1
        while True:  # markers after the punctuation belong to this sentence
            match = _TRAILING_MARKERS.match(text, j)
            if not match:
                break
            j = match.end()
        sentences.append(text[start:j])
        start = i = j
    if start < len(text):
        sentences.append(text[start:])
    return [s.strip() for s in sentences if s.strip()]


def _labels_in(sentence: str) -> list[str]:
    found: list[str] = []
    for marker in _MARKER.finditer(sentence):
        found.extend(_LABEL.findall(marker.group(1)))
    return found


def _split_labels(sentence: str, known: set[str]) -> tuple[list[str], int]:
    """The valid labels cited in a sentence, in order of first use, and how many labels the model
    wrote that were not supplied in this request."""
    written = _labels_in(sentence)
    valid = [label for label in written if label in known]
    return list(dict.fromkeys(valid)), len(written) - len(valid)


def _canonical(sentence: str, known: set[str], *, keep_markers: bool) -> str:
    """The sentence with only valid markers, each where the model put it.

    Unknown labels are dropped (a marker left empty disappears together with the space before it),
    `[S1, S2]` becomes `[S1][S2]`, and markers written after the final punctuation move in front of
    it (`July. [S1]` becomes `July [S1].`) unless the same labels already appear earlier in the
    sentence. No character of the checked text changes. With `keep_markers` false (answer-level
    citations) every marker is removed.
    """

    def rewrite(match: re.Match[str]) -> str:
        valid = [label for label in _LABEL.findall(match.group(2)) if label in known]
        if not keep_markers or not valid:
            return ""
        return match.group(1) + "".join(f"[{label}]" for label in dict.fromkeys(valid))

    text = " ".join(re.sub(r"(\s*)" + _MARKER.pattern, rewrite, sentence).split())
    tail = re.search(r"([.!?।॥]+)\s*((?:\[S\d+\])+)$", text)
    if tail:
        head = text[: tail.start()]
        trailing = re.findall(r"\[S\d+\]", tail.group(2))
        present = set(re.findall(r"\[S\d+\]", head))
        extra = [m for m in dict.fromkeys(trailing) if m not in present]
        text = f"{head}{' ' if extra else ''}{''.join(extra)}{tail.group(1)}"
    return text.strip()


def _has_content(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _quotes_supported(body: str, cited: list[ContextBlock]) -> bool:
    sources = [normalise_for_matching(block.text) for block in cited]
    for match in _QUOTE.finditer(body):
        quote = normalise_for_matching(next(group for group in match.groups() if group))
        if not any(quote in source for source in sources):  # inside one source, not spliced
            return False
    return True


def _literals_supported(body: str, cited: list[ContextBlock]) -> bool:
    haystack = "\n".join(normalise_for_matching(block.text) for block in cited)
    for token in _LITERAL.findall(normalise_for_matching(body)):
        token = token.strip(".,-_/:")
        if not token:
            continue
        pattern = r"(?<!\w)(?<!\d[.,])" + re.escape(token) + r"(?!\w)(?![.,]\d)"
        if not re.search(pattern, haystack):
            return False
    return True


def ground_answer(output: ModelOutput, blocks: list[ContextBlock]) -> GroundedAnswer:
    """Apply the deterministic checks to a parsed completion."""
    if output.answerable is False or not output.answer.strip():
        return GroundedAnswer(False, "", [], reason="model_abstained")
    by_label = {block.label: block for block in blocks}
    counts = _Counts()
    sentences = split_sentences(unicodedata.normalize("NFC", output.answer))
    # Some models fill the `citations` array but forget the inline markers. If no sentence has a
    # marker at all, the labels the model declared (valid ones only) apply to the whole answer, and
    # the response says the citation is answer-level, not sentence-level.
    declared = list(
        dict.fromkeys(
            label
            for item in output.citations
            for label in _LABEL.findall(item)
            if label in by_label
        )
    )
    answer_level = bool(declared) and not any(_labels_in(s) for s in sentences)
    for sentence in sentences:
        labels, removed = _split_labels(sentence, set(by_label))
        if answer_level:
            labels = declared
        counts.unknown += removed
        body = _MARKER.sub("", sentence)
        if not _has_content(body):
            continue  # punctuation or a bare marker
        if not labels:
            counts.uncited += 1
            continue
        cited = [by_label[label] for label in labels]
        if not _quotes_supported(body, cited):
            counts.quotes += 1
            continue
        if not _literals_supported(body, cited):
            counts.literals += 1
            continue
        counts.kept.append(_canonical(sentence, set(by_label), keep_markers=not answer_level))
        counts.labels.extend(labels)
    if not counts.kept:
        return GroundedAnswer(
            False, "", [], counts.unknown, counts.uncited, counts.quotes, counts.literals,
            reason="no_supported_sentences",
        )  # fmt: skip
    text = " ".join(counts.kept)
    if answer_level:
        text += " " + "".join(f"[{label}]" for label in declared)
    return GroundedAnswer(
        True, text, list(dict.fromkeys(counts.labels)), counts.unknown,
        counts.uncited, counts.quotes, counts.literals,
        granularity="answer" if answer_level else "sentence",
    )  # fmt: skip


def insufficient_message(question: str, *, search_degraded: bool = False) -> str:
    from app.rag.tokens import bengali_share

    bangla = bengali_share(question) >= 0.3
    if search_degraded:
        return (
            "অনুসন্ধানের একটি অংশ সাময়িকভাবে অনুপলব্ধ ছিল, তাই উত্তর দেওয়া যায়নি।"
            if bangla
            else "Part of the search was unavailable, so the question could not be answered."
        )
    if bangla:
        return "প্রদত্ত নথিগুলোতে এই প্রশ্নের উত্তর দেওয়ার মতো যথেষ্ট তথ্য পাওয়া যায়নি।"
    return "The provided documents do not contain enough information to answer this question."


def excerpt(text: str, limit: int = 300) -> tuple[str, bool]:
    """A bounded excerpt of the text shown to the model, cut on a word boundary when possible."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat, False
    cut = flat[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip() + "…", True
