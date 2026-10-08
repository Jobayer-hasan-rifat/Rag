from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.documents.processing import Deadline, ExtractionLimits


class SectionKind(StrEnum):
    PAGE = "page"  # one PDF page; `page_number` is set
    SECTION = "section"  # a heading-delimited block (DOCX, Markdown)
    BODY = "body"  # unstructured text (plain text, or content before the first heading)


@dataclass(frozen=True)
class ExtractedSection:
    kind: SectionKind
    text: str
    page_number: int | None = None
    heading: str | None = None
    heading_level: int | None = None


@dataclass
class ExtractionResult:
    sections: list[ExtractedSection]
    extractor: str
    page_count: int | None = None
    properties: dict[str, Any] = field(default_factory=dict)  # untrusted file-embedded metadata


class DocumentExtractor(ABC):
    """Turns raw file bytes into ordered sections of *raw* text.

    Implementations treat the input as untrusted data: they never execute, render or follow
    anything inside it, and they enforce the supplied limits while extracting. Normalisation
    is a separate stage applied by the pipeline.
    """

    name: str
    collapse_inline_spaces: bool = True

    @abstractmethod
    def extract(
        self, data: bytes, *, limits: ExtractionLimits, deadline: Deadline
    ) -> ExtractionResult: ...
