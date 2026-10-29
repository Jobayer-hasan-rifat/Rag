import contextlib
from dataclasses import dataclass
from typing import Any

import pymupdf

from app.core.documents.processing import (
    Deadline,
    ExtractionLimits,
    FailureReason,
    ProcessingFailure,
)
from app.observability.logging import get_logger
from app.parsers.base import DocumentExtractor, ExtractedSection, ExtractionResult, SectionKind
from app.parsers.ocr import OcrConfig, OcrEngine, create_engine, tesseract_available

logger = get_logger("app.parsers.pdf")
MAX_RENDER_PIXELS = 6000  # longest side of a page image given to OCR; larger pages are scaled down

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


@dataclass
class _OcrStats:
    recognised: int = 0
    skipped: int = 0  # scanned pages left unread because the per-document page limit was reached
    failed: int = 0


class PDFExtractor(DocumentExtractor):
    """Text extraction with PyMuPDF, one section per page so page numbers are preserved.

    PyMuPDF never executes JavaScript or launches embedded content. A page is rendered only when
    OCR is configured and the page has no text layer (see `parsers/ocr.py`).
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
            engine = _ocr_engine(limits.ocr)
            stats = _OcrStats()
            sections, unreadable = self._read_pages(
                document, page_count, limits, deadline, engine, stats
            )
            if unreadable == page_count:
                raise ProcessingFailure(FailureReason.CORRUPT_DOCUMENT, detail="no readable page")
            properties = _properties(document.metadata)
            if unreadable:
                properties["unreadable_pages"] = unreadable
            for key, value in (
                ("ocr_pages", stats.recognised),
                ("ocr_skipped_pages", stats.skipped),
                ("ocr_failed_pages", stats.failed),
            ):
                if value:
                    properties[key] = value
            if stats.skipped:
                logger.warning(
                    "scanned pages left unread: OCR page limit reached",
                    extra={"skipped_pages": stats.skipped},
                )
        return ExtractionResult(
            sections=sections, extractor=self.name, page_count=page_count, properties=properties
        )

    @staticmethod
    def _read_pages(
        document: Any,
        page_count: int,
        limits: ExtractionLimits,
        deadline: Deadline,
        engine: OcrEngine | None,
        stats: _OcrStats,
    ) -> tuple[list[ExtractedSection], int]:
        sections: list[ExtractedSection] = []
        total_chars = 0
        unreadable = 0
        for index in range(page_count):
            deadline.check()
            try:
                page = document.load_page(index)
                text: str = page.get_text("text")
            except Exception:
                page = None
                text = ""
                unreadable += 1
            if page is not None and engine is not None and limits.ocr is not None:
                text = _recognise_if_scanned(page, text, limits.ocr, engine, deadline, stats)
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


def _ocr_engine(config: OcrConfig | None) -> OcrEngine | None:
    if config is None:
        return None
    if not tesseract_available(config):
        logger.error("OCR is enabled but the Tesseract program was not found; scans stay unread")
        return None
    return create_engine(config)


def _recognise_if_scanned(
    page: Any,
    text: str,
    config: OcrConfig,
    engine: OcrEngine,
    deadline: Deadline,
    stats: _OcrStats,
) -> str:
    """The text of the page, or the OCR of its image when it has (almost) no text layer.

    Only a page with no text that does contain an image is recognised: a blank page costs
    nothing. A failed page is counted and left empty; it never fails the document.
    """
    if sum(1 for char in text if not char.isspace()) >= config.min_text_chars:
        return text
    try:
        has_image = bool(page.get_images())
    except Exception:
        has_image = False
    if not has_image:
        return text
    if stats.recognised + stats.failed >= config.max_pages:
        stats.skipped += 1
        return text
    deadline.check()
    try:
        width, height = page.rect.width, page.rect.height
        dpi = min(config.dpi, int(MAX_RENDER_PIXELS * 72 / max(width, height, 1)))
        image = page.get_pixmap(dpi=max(dpi, 72)).tobytes("png")
        recognised = engine.recognise(image)
    except ProcessingFailure:
        stats.failed += 1
        return text
    except Exception:
        stats.failed += 1
        return text
    stats.recognised += 1
    return recognised
