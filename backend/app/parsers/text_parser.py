import re

from app.core.documents.processing import (
    Deadline,
    ExtractionLimits,
    FailureReason,
    ProcessingFailure,
)
from app.parsers.base import DocumentExtractor, ExtractedSection, ExtractionResult, SectionKind

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_DEADLINE_EVERY = 5000


def _decode(data: bytes, limits: ExtractionLimits) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ProcessingFailure(FailureReason.CORRUPT_DOCUMENT, detail="not utf-8") from None
    if len(text) > limits.max_text_chars:
        raise ProcessingFailure(FailureReason.CONTENT_TOO_LARGE)
    return text


class TextExtractor(DocumentExtractor):
    """Plain text: one body section. Interior spacing is preserved."""

    name = "text"
    collapse_inline_spaces = False

    def extract(
        self, data: bytes, *, limits: ExtractionLimits, deadline: Deadline
    ) -> ExtractionResult:
        deadline.check()
        text = _decode(data, limits)
        return ExtractionResult(
            sections=[ExtractedSection(kind=SectionKind.BODY, text=text)], extractor=self.name
        )


class MarkdownExtractor(DocumentExtractor):
    """Markdown split at ATX headings (`# Title`), ignoring headings inside code fences.

    The Markdown source is kept verbatim as text: nothing is rendered, and embedded HTML or
    scripts are inert strings.
    """

    name = "markdown"
    collapse_inline_spaces = False

    def extract(
        self, data: bytes, *, limits: ExtractionLimits, deadline: Deadline
    ) -> ExtractionResult:
        text = _decode(data, limits)
        sections: list[ExtractedSection] = []
        heading: str | None = None
        level: int | None = None
        lines: list[str] = []
        fence: str | None = None

        def flush() -> None:
            if heading is None and not any(line.strip() for line in lines):
                return
            sections.append(
                ExtractedSection(
                    kind=SectionKind.SECTION if heading is not None else SectionKind.BODY,
                    text="\n".join(lines),
                    heading=heading,
                    heading_level=level,
                )
            )

        for number, line in enumerate(text.splitlines()):
            if number % _DEADLINE_EVERY == 0:
                deadline.check()
            fence_match = _FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence is None:
                    fence = marker[0] * len(marker)
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = None
                lines.append(line)
                continue
            heading_match = None if fence else _ATX_HEADING.match(line)
            if heading_match and heading_match.group(2):
                flush()
                heading, level = heading_match.group(2), len(heading_match.group(1))
                lines = []
            else:
                lines.append(line)
        flush()
        return ExtractionResult(sections=sections, extractor=self.name)
