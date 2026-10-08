import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

PROCESSING_VERSION = "p4.1"


class FailureReason(StrEnum):
    """Machine-readable failure codes stored on a document and shown to its owner."""

    STORAGE_MISSING = "storage_missing"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    CORRUPT_DOCUMENT = "corrupt_document"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    UNSUPPORTED_FORMAT = "unsupported_format"
    EMPTY_DOCUMENT = "empty_document"
    TOO_MANY_PAGES = "too_many_pages"
    CONTENT_TOO_LARGE = "content_too_large"
    TIMEOUT = "timeout"
    EXTRACTION_FAILED = "extraction_failed"
    DATABASE_ERROR = "database_error"
    TOO_MANY_CHUNKS = "too_many_chunks"
    CHUNKING_FAILED = "chunking_failed"
    RETRIES_EXHAUSTED = "retries_exhausted"


SAFE_MESSAGES: dict[FailureReason, str] = {
    FailureReason.STORAGE_MISSING: "The stored file could not be found.",
    FailureReason.STORAGE_UNAVAILABLE: "File storage was unavailable during processing.",
    FailureReason.CORRUPT_DOCUMENT: "The document appears to be damaged and could not be read.",
    FailureReason.ENCRYPTED_DOCUMENT: "Password-protected documents are not supported.",
    FailureReason.UNSUPPORTED_FORMAT: "This document format cannot be processed.",
    FailureReason.EMPTY_DOCUMENT: (
        "No extractable text was found. Scanned or image-only documents need OCR, "
        "which is not supported."
    ),
    FailureReason.TOO_MANY_PAGES: "The document has more pages than the processing limit.",
    FailureReason.CONTENT_TOO_LARGE: "The document contains more text than the processing limit.",
    FailureReason.TIMEOUT: "Processing took too long and was stopped.",
    FailureReason.EXTRACTION_FAILED: "Text extraction failed unexpectedly.",
    FailureReason.DATABASE_ERROR: "Processing results could not be saved.",
    FailureReason.TOO_MANY_CHUNKS: (
        "The document would produce more chunks than the processing limit."
    ),
    FailureReason.CHUNKING_FAILED: "The extracted text could not be split into chunks.",
    FailureReason.RETRIES_EXHAUSTED: "Processing was interrupted repeatedly and was abandoned.",
}


class ProcessingFailure(Exception):
    """A classified processing error. Messages must never contain document content."""

    def __init__(self, reason: FailureReason, *, retryable: bool = False, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.retryable = retryable
        self.detail = detail


@dataclass(frozen=True)
class ExtractionLimits:
    max_pages: int
    max_text_chars: int
    max_docx_uncompressed_bytes: int


class Deadline:
    """Cooperative time budget checked between pages/sections (works in any Celery pool)."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._expires_at = clock() + seconds

    def check(self) -> None:
        if self._clock() >= self._expires_at:
            raise ProcessingFailure(FailureReason.TIMEOUT)
