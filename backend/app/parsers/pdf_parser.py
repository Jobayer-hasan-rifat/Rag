import contextlib
from typing import Any

import pymupdf

from app.core.documents.processing import (
    Deadline,
    ExtractionLimits,
    FailureReason,
    ProcessingFailure,
)
from app.parsers.base import DocumentExtractor, ExtractedSection, ExtractionResult, SectionKind

_PROPERTY_KEYS = {
    "title": "title",
    "author": "author",
    "subject": "subject",
    "keywords": "keywords",
    "creator": "creator",
    "producer": "producer",
    "creationDate": "created",
    "modDate": "modified",
}


class PDFExtractor(DocumentExtractor):
    """Text extraction with PyMuPDF, one section per page so page numbers are preserved.

    PyMuPDF never executes JavaScript or launches embedded content; pages are never rendered.
    """

    name = "pdf"

    def extract(
        self, data: bytes, *, limits: ExtractionLimits, deadline: Deadline
    ) -> ExtractionResult:
        pymupdf.TOOLS.mupdf_display_errors(False)
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as error:
            raise ProcessingFailure(
                FailureReason.CORRUPT_DOCUMENT, detail=type(error).__name__
            ) from None

        with contextlib.closing(document):
            if document.needs_pass:
                raise ProcessingFailure(FailureReason.ENCRYPTED_DOCUMENT)
            page_count = document.page_count
            if page_count == 0:
                raise ProcessingFailure(FailureReason.CORRUPT_DOCUMENT, detail="no pages")
            if page_count > limits.max_pages:
                raise ProcessingFailure(
                    FailureReason.TOO_MANY_PAGES, detail=f"{page_count} > {limits.max_pages}"
                )
            sections, unreadable = self._read_pages(document, page_count, limits, deadline)
            if unreadable == page_count:
                raise ProcessingFailure(FailureReason.CORRUPT_DOCUMENT, detail="no readable page")
            properties = _properties(document.metadata)
            if unreadable:
                properties["unreadable_pages"] = unreadable
        return ExtractionResult(
            sections=sections, extractor=self.name, page_count=page_count, properties=properties
        )

    @staticmethod
    def _read_pages(
        document: Any, page_count: int, limits: ExtractionLimits, deadline: Deadline
    ) -> tuple[list[ExtractedSection], int]:
        sections: list[ExtractedSection] = []
        total_chars = 0
        unreadable = 0
        for index in range(page_count):
            deadline.check()
            try:
                text: str = document.load_page(index).get_text("text")
            except Exception:
                text = ""
                unreadable += 1
            total_chars += len(text)
            if total_chars > limits.max_text_chars:
                raise ProcessingFailure(FailureReason.CONTENT_TOO_LARGE)
            sections.append(
                ExtractedSection(kind=SectionKind.PAGE, text=text, page_number=index + 1)
            )
        return sections, unreadable


def _properties(metadata: dict[str, Any] | None) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for source, target in _PROPERTY_KEYS.items():
        value = (metadata or {}).get(source)
        if isinstance(value, str) and value.strip():
            found[target] = value
    return found
