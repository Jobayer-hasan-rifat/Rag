"""Optical character recognition for pages that have no text layer (scanned documents).

The engine is Tesseract, the widely used open-source OCR project (github.com/tesseract-ocr), run as
a separate process. PyMuPDF renders the page to a PNG that this module owns, so Tesseract never
sees the uploaded file itself, only an image the worker produced. The image goes in on standard
input and the text comes back on standard output: no temporary files, and the argument list is
fixed (no shell, languages validated), so nothing from a document can reach a command line.

Why the command line and not PyMuPDF's built-in Tesseract binding: both use Tesseract, but the
binding dropped the spaces between Bengali words in testing, which would hurt keyword search and
embeddings; the command line kept them.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.documents.processing import FailureReason, ProcessingFailure
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger("app.ocr")

LANGUAGES = re.compile(r"[a-z]{3}(?:_[a-z]+)?(?:\+[a-z]{3}(?:_[a-z]+)?)*")
MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # a page of text is a few kilobytes; this only bounds a fault


@dataclass(frozen=True)
class OcrConfig:
    languages: str  # Tesseract language codes joined with "+", for example "eng+ben"
    dpi: int  # resolution a page is rendered at before recognition
    max_pages: int  # pages recognised per document; further blank-text pages are left empty
    page_timeout_seconds: int
    page_segmentation_mode: int  # Tesseract --psm (3: automatic layout analysis)
    min_text_chars: int  # a page with fewer non-space characters than this is treated as scanned
    executable: str = "tesseract"

    def __post_init__(self) -> None:
        if not LANGUAGES.fullmatch(self.languages):
            raise ValueError("OCR languages must look like 'eng+ben'")


class OcrEngine:
    """Turns one page image (PNG bytes) into text."""

    def recognise(self, image_png: bytes) -> str:
        raise NotImplementedError


Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


class TesseractEngine(OcrEngine):
    def __init__(self, config: OcrConfig, runner: Runner | None = None) -> None:
        self._config = config
        self._run: Runner = runner or subprocess.run

    def _command(self) -> list[str]:
        config = self._config
        return [
            config.executable,
            "stdin",
            "stdout",
            "-l",
            config.languages,
            "--psm",
            str(config.page_segmentation_mode),
            "--oem",
            "1",  # the neural (LSTM) engine only
        ]

    def recognise(self, image_png: bytes) -> str:
        # one thread per process: the worker already runs several documents at once
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            result = self._run(
                self._command(),
                input=image_png,
                capture_output=True,
                timeout=self._config.page_timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise ProcessingFailure(FailureReason.TIMEOUT, detail="ocr page timeout") from None
        except OSError:
            raise ProcessingFailure(
                FailureReason.EXTRACTION_FAILED, detail="ocr unavailable"
            ) from None
        if result.returncode != 0:
            logger.warning("ocr failed", extra={"returncode": result.returncode})
            raise ProcessingFailure(FailureReason.EXTRACTION_FAILED, detail="ocr failed")
        if len(result.stdout) > MAX_OUTPUT_BYTES:
            raise ProcessingFailure(FailureReason.CONTENT_TOO_LARGE, detail="ocr output")
        return result.stdout.decode("utf-8", errors="replace")


def tesseract_available(config: OcrConfig) -> bool:
    return shutil.which(config.executable) is not None


EngineFactory = Callable[[OcrConfig], OcrEngine]
_factory: EngineFactory = TesseractEngine


def create_engine(config: OcrConfig) -> OcrEngine:
    return _factory(config)


def set_engine_factory(factory: EngineFactory | None) -> None:
    """Replace the engine (tests register a deterministic fake); None restores Tesseract."""
    global _factory
    _factory = factory or TesseractEngine


def ocr_config(settings: "Settings") -> OcrConfig | None:
    """The OCR settings, or None when OCR is switched off."""
    if not settings.ocr_enabled:
        return None
    return OcrConfig(
        languages=settings.ocr_languages,
        dpi=settings.ocr_dpi,
        max_pages=settings.ocr_max_pages,
        page_timeout_seconds=settings.ocr_page_timeout_seconds,
        page_segmentation_mode=settings.ocr_page_segmentation_mode,
        min_text_chars=settings.ocr_min_text_chars,
    )
