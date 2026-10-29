"""OCR for scanned PDF pages: the Tesseract wrapper, the extractor's decisions, and (where
Tesseract is installed, as in the Docker image) a real recognition."""

import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from app.config import Settings
from app.core.documents.processing import (
    Deadline,
    ExtractionLimits,
    FailureReason,
    ProcessingFailure,
)
from app.parsers import ocr
from app.parsers.ocr import OcrConfig, OcrEngine, TesseractEngine, ocr_config, set_engine_factory
from app.parsers.pdf_parser import PDFExtractor
from tests.conftest import SettingsFactory
from tests.pdf_factory import huge_scan_pdf, mixed_pdf, png_size

CONFIG = OcrConfig(
    languages="eng+ben",
    dpi=200,
    max_pages=3,
    page_timeout_seconds=30,
    page_segmentation_mode=3,
    min_text_chars=25,
)


def limits(config: OcrConfig | None = CONFIG) -> ExtractionLimits:
    return ExtractionLimits(
        max_pages=100,
        max_text_chars=1_000_000,
        max_docx_uncompressed_bytes=10_000_000,
        ocr=config,
    )


def deadline(seconds: float = 60) -> Deadline:
    return Deadline(seconds)


class FakeEngine(OcrEngine):
    def __init__(self, replies: list[str | Exception] | None = None) -> None:
        self.images: list[bytes] = []
        self._replies = replies or []

    def recognise(self, image_png: bytes) -> str:
        self.images.append(image_png)
        reply: str | Exception = (
            self._replies.pop(0) if self._replies else f"recognised page {len(self.images)}"
        )
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    monkeypatch.setattr("app.parsers.pdf_parser.tesseract_available", lambda config: True)
    set_engine_factory(lambda config: fake)
    yield fake
    set_engine_factory(None)


# --- the extractor's decisions -------------------------------------------------------------


def test_a_scanned_page_is_recognised_and_keeps_its_page_number(engine: FakeEngine) -> None:
    result = PDFExtractor().extract(mixed_pdf("scan:hello"), limits=limits(), deadline=deadline())

    assert [s.text for s in result.sections] == ["recognised page 1"]
    assert [s.page_number for s in result.sections] == [1]
    assert result.properties["ocr_pages"] == 1


def test_pages_with_a_text_layer_are_never_sent_to_ocr(engine: FakeEngine) -> None:
    data = mixed_pdf("text:This page already has plenty of ordinary text on it.", "scan:x", "blank")

    result = PDFExtractor().extract(data, limits=limits(), deadline=deadline())

    assert len(engine.images) == 1  # only the scanned page
    assert result.sections[0].text.startswith("This page already has")
    assert result.sections[2].text.strip() == ""  # a blank page (no image) costs nothing


def test_the_image_handed_to_ocr_is_a_png_made_by_the_worker(engine: FakeEngine) -> None:
    PDFExtractor().extract(mixed_pdf("scan:hello"), limits=limits(), deadline=deadline())

    assert engine.images[0].startswith(b"\x89PNG\r\n\x1a\n")


def test_a_page_limit_leaves_further_scans_unread_and_says_so(engine: FakeEngine) -> None:
    data = mixed_pdf(*["scan:page"] * 5)

    result = PDFExtractor().extract(data, limits=limits(), deadline=deadline())

    assert len(engine.images) == 3
    assert result.properties["ocr_pages"] == 3
    assert result.properties["ocr_skipped_pages"] == 2
    assert [s.text.strip() == "" for s in result.sections] == [False, False, False, True, True]


def test_a_failing_page_is_counted_and_never_fails_the_document(engine: FakeEngine) -> None:
    engine._replies = [ProcessingFailure(FailureReason.EXTRACTION_FAILED), "second page text"]

    result = PDFExtractor().extract(
        mixed_pdf("scan:a", "scan:b"), limits=limits(), deadline=deadline()
    )

    assert [s.text for s in result.sections] == ["", "second page text"]
    assert result.properties["ocr_failed_pages"] == 1
    assert result.properties["ocr_pages"] == 1


def test_an_unexpected_engine_error_is_also_contained(engine: FakeEngine) -> None:
    engine._replies = [RuntimeError("boom")]

    result = PDFExtractor().extract(mixed_pdf("scan:a"), limits=limits(), deadline=deadline())

    assert result.properties["ocr_failed_pages"] == 1


def test_an_expired_deadline_stops_before_the_next_page_is_recognised(engine: FakeEngine) -> None:
    expired = Deadline(0)

    with pytest.raises(ProcessingFailure) as raised:
        PDFExtractor().extract(mixed_pdf("scan:a"), limits=limits(), deadline=expired)

    assert raised.value.reason == FailureReason.TIMEOUT
    assert engine.images == []


def test_without_ocr_a_scanned_page_stays_empty_as_before() -> None:
    result = PDFExtractor().extract(
        mixed_pdf("scan:hello"), limits=limits(None), deadline=deadline()
    )

    assert result.sections[0].text.strip() == ""
    assert "ocr_pages" not in result.properties


def test_ocr_on_but_tesseract_missing_leaves_scans_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.parsers.pdf_parser.tesseract_available", lambda config: False)
    called = FakeEngine()
    set_engine_factory(lambda config: called)
    try:
        result = PDFExtractor().extract(mixed_pdf("scan:a"), limits=limits(), deadline=deadline())
    finally:
        set_engine_factory(None)

    assert called.images == []
    assert result.sections[0].text.strip() == ""


def test_a_huge_page_is_scaled_down_before_recognition(engine: FakeEngine) -> None:
    high = OcrConfig("eng", 600, 3, 30, 3, 25)

    PDFExtractor().extract(huge_scan_pdf(), limits=limits(high), deadline=deadline())

    assert max(png_size(engine.images[0])) <= 6100


# --- the Tesseract wrapper -----------------------------------------------------------------


class Recorder:
    def __init__(self, result: Any) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._result = result

    def __call__(self, command: list[str], **kwargs: Any) -> Any:
        self.calls.append((command, kwargs))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def completed(stdout: bytes = b"text", returncode: int = 0) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


def test_tesseract_gets_a_fixed_argument_list_and_the_image_on_stdin() -> None:
    runner = Recorder(completed("ইলিশ মাছ".encode()))

    text = TesseractEngine(CONFIG, runner).recognise(b"png-bytes")

    command, kwargs = runner.calls[0]
    assert command == ["tesseract", "stdin", "stdout", "-l", "eng+ben", "--psm", "3", "--oem", "1"]
    assert kwargs["input"] == b"png-bytes"
    assert kwargs["timeout"] == 30
    assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
    assert "shell" not in kwargs  # never a shell
    assert text == "ইলিশ মাছ"


@pytest.mark.parametrize(
    "languages", ["eng; rm -rf /", "eng ben", "eng+", "ENG", "", "eng|ben", "../x"]
)
def test_a_language_setting_cannot_carry_a_command(languages: str) -> None:
    with pytest.raises(ValueError, match="languages"):
        OcrConfig(languages, 300, 10, 30, 3, 25)


def test_a_page_that_takes_too_long_is_a_timeout_failure() -> None:
    runner = Recorder(subprocess.TimeoutExpired(cmd="tesseract", timeout=30))

    with pytest.raises(ProcessingFailure) as raised:
        TesseractEngine(CONFIG, runner).recognise(b"x")

    assert raised.value.reason == FailureReason.TIMEOUT


def test_a_missing_or_failing_program_is_an_extraction_failure() -> None:
    for runner in (Recorder(FileNotFoundError()), Recorder(completed(returncode=1))):
        with pytest.raises(ProcessingFailure) as raised:
            TesseractEngine(CONFIG, runner).recognise(b"x")
        assert raised.value.reason == FailureReason.EXTRACTION_FAILED


def test_runaway_output_is_refused() -> None:
    runner = Recorder(completed(b"x" * (ocr.MAX_OUTPUT_BYTES + 1)))

    with pytest.raises(ProcessingFailure) as raised:
        TesseractEngine(CONFIG, runner).recognise(b"x")

    assert raised.value.reason == FailureReason.CONTENT_TOO_LARGE


# --- settings --------------------------------------------------------------------------------


def test_ocr_is_off_by_default_and_the_bridge_reflects_the_settings(
    make_settings: SettingsFactory,
) -> None:
    # the code default is off; an OCR_ENABLED variable in the environment may say otherwise
    assert Settings.model_fields["ocr_enabled"].default is False
    assert ocr_config(make_settings(ocr_enabled=False)) is None

    settings = make_settings(ocr_enabled=True, ocr_languages="eng", ocr_dpi=250, ocr_max_pages=7)

    assert ocr_config(settings) == OcrConfig("eng", 250, 7, 30, 3, 25)


@pytest.mark.parametrize("bad", ["eng ben", "eng+", "e", "ENG+ben", "eng;ls"])
def test_invalid_ocr_languages_are_refused_at_start(
    make_settings: SettingsFactory, bad: str
) -> None:
    with pytest.raises(ValueError, match="OCR_LANGUAGES"):
        make_settings(ocr_languages=bad)


def test_ocr_settings_have_sane_bounds(make_settings: SettingsFactory) -> None:
    for name, value in (("ocr_dpi", 50), ("ocr_dpi", 900), ("ocr_max_pages", 0)):
        with pytest.raises(ValueError):
            make_settings(**{name: value})
    assert isinstance(make_settings(), Settings)


# --- the real engine (runs only where Tesseract and its language data are installed) --------


def _tesseract_with_english() -> bool:
    if shutil.which("tesseract") is None:
        return False
    program = shutil.which("tesseract") or "tesseract"
    listed = subprocess.run(  # noqa: S603 (a fixed command, no input)
        [program, "--list-langs"], capture_output=True, text=True, check=False
    )
    return "eng" in listed.stdout.split()


real_tesseract = pytest.mark.skipif(
    not _tesseract_with_english(), reason="Tesseract with English data is not installed"
)


@real_tesseract
def test_tesseract_reads_a_scanned_english_page() -> None:
    text = (
        "The brood-fish ban lasts 22 consecutive days.\n"
        "Registered fishers receive 40 kilograms of rice."
    )
    config = OcrConfig("eng", 300, 5, 60, 3, 25)

    result = PDFExtractor().extract(
        mixed_pdf(f"scan:{text}"), limits=limits(config), deadline=deadline()
    )

    recognised = " ".join(result.sections[0].text.split())
    assert "brood-fish ban lasts 22 consecutive days" in recognised
    assert "40 kilograms of rice" in recognised
