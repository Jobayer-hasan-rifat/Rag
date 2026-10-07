import io
import zipfile

import pytest

from app.core.documents.types import DocumentType
from app.exceptions import (
    FileTooLargeError,
    InvalidFileError,
    UnsupportedFileTypeError,
)
from app.security.file_validation import (
    document_type_for_filename,
    inspect_upload,
    sanitize_filename,
)
from tests.files import DOCX_TYPE, docx_bytes, markdown_bytes, pdf_bytes, text_bytes

MAX = 1024 * 1024


def _inspect(content: bytes, name: str, declared: str | None = None, max_bytes: int = MAX):  # type: ignore[no-untyped-def]
    return inspect_upload(io.BytesIO(content), name, declared, max_bytes)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
        ("/abs/path/file.pdf", "file.pdf"),
        ("C:\\Users\\bob\\file.docx", "file.docx"),
        ("  spaced   name .txt  ", "spaced name .txt"),
        ("null\x00byte.pdf", "nullbyte.pdf"),
        ("line\nbreak\r\n.md", "linebreak.md"),
        ("trailing.dots.pdf...", "trailing.dots.pdf"),
        ("evil\u202egnp.pdf", "evilgnp.pdf"),
        ("e\u0301.txt", "\u00e9.txt"),
        ("héllo wörld.md", "héllo wörld.md"),
    ],
)
def test_filenames_are_reduced_to_a_safe_display_name(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", ".", "..", "/", "\\", "...", "\x00\x01"])
def test_unusable_filenames_are_rejected(raw: str | None) -> None:
    with pytest.raises(InvalidFileError):
        sanitize_filename(raw)


def test_sanitised_names_never_contain_path_separators_or_control_characters() -> None:
    nasty = "a/b\\c\x00d\x1fe\x7ff\u200b.pdf"

    cleaned = sanitize_filename(nasty)

    assert "/" not in cleaned and "\\" not in cleaned
    assert all(ord(char) >= 32 and ord(char) != 127 for char in cleaned)


def test_overlong_names_are_truncated_but_keep_their_extension() -> None:
    cleaned = sanitize_filename("x" * 400 + ".pdf")

    assert len(cleaned) == 255
    assert cleaned.endswith(".pdf")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.pdf", DocumentType.PDF),
        ("A.PDF", DocumentType.PDF),
        ("a.docx", DocumentType.DOCX),
        ("a.txt", DocumentType.TXT),
        ("a.md", DocumentType.MD),
        ("evil.php.pdf", DocumentType.PDF),
    ],
)
def test_supported_extensions_map_to_types(name: str, expected: DocumentType) -> None:
    assert document_type_for_filename(name) is expected


@pytest.mark.parametrize(
    "name", ["a.exe", "a.pdf.exe", "a.html", "a.svg", "a.zip", "a.doc", "a", "pdf", "a.markdown"]
)
def test_unsupported_extensions_are_rejected(name: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        document_type_for_filename(name)


def test_valid_pdf_is_accepted_and_hashed() -> None:
    result = _inspect(pdf_bytes(), "a.pdf", "application/pdf")

    assert result.document_type is DocumentType.PDF
    assert result.content_type == "application/pdf"
    assert len(result.sha256) == 64
    assert result.size_bytes == len(pdf_bytes())


def test_valid_docx_text_and_markdown_are_accepted() -> None:
    assert _inspect(docx_bytes(), "a.docx", DOCX_TYPE).document_type is DocumentType.DOCX
    assert _inspect(text_bytes(), "a.txt", "text/plain").document_type is DocumentType.TXT
    assert _inspect(markdown_bytes(), "a.md", "text/markdown").document_type is DocumentType.MD
    assert _inspect("héllo wörld".encode(), "a.txt").document_type is DocumentType.TXT


def test_same_content_gives_same_checksum() -> None:
    assert _inspect(pdf_bytes("x"), "a.pdf").sha256 == _inspect(pdf_bytes("x"), "b.pdf").sha256
    assert _inspect(pdf_bytes("x"), "a.pdf").sha256 != _inspect(pdf_bytes("y"), "a.pdf").sha256


@pytest.mark.parametrize("declared", [None, "", "application/octet-stream"])
def test_unspecified_content_types_are_tolerated(declared: str | None) -> None:
    assert _inspect(pdf_bytes(), "a.pdf", declared).document_type is DocumentType.PDF


@pytest.mark.parametrize(
    "declared", ["text/html", "image/png", "application/x-msdownload", "text/plain"]
)
def test_declared_type_contradicting_the_extension_is_rejected(declared: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        _inspect(pdf_bytes(), "a.pdf", declared)


def test_markdown_may_be_declared_as_plain_text() -> None:
    assert _inspect(markdown_bytes(), "a.md", "text/plain; charset=utf-8")


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b"<html><script>alert(1)</script></html>", "a.pdf"),
        (b"MZ\x90\x00 this is a windows executable", "a.pdf"),
        (b"%PDF-1.4 truncated, no end marker", "a.pdf"),
        (b"just text pretending to be a pdf", "a.pdf"),
        (pdf_bytes(), "a.docx"),
        (b"PK\x03\x04 not really a zip", "a.docx"),
    ],
)
def test_content_that_does_not_match_the_extension_is_rejected(content: bytes, name: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        _inspect(content, name)


def test_docx_without_required_parts_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "x")

    with pytest.raises(UnsupportedFileTypeError):
        _inspect(buffer.getvalue(), "a.docx")


def test_zip_bomb_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "0" * 50_000_000)

    with pytest.raises(UnsupportedFileTypeError):
        _inspect(buffer.getvalue(), "a.docx", max_bytes=10 * 1024 * 1024)


@pytest.mark.parametrize(
    "content", [b"binary\x00data", b"\xff\xfe\x00\x01", "caf\xe9".encode("latin-1"), b"ok\xc3"]
)
def test_non_utf8_or_binary_text_files_are_rejected(content: bytes) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        _inspect(content, "a.txt")


def test_empty_files_are_rejected() -> None:
    with pytest.raises(InvalidFileError):
        _inspect(b"", "a.txt")


def test_oversized_files_are_rejected_without_reading_everything() -> None:
    content = b"a" * 10_000

    with pytest.raises(FileTooLargeError) as error:
        inspect_upload(io.BytesIO(content), "a.txt", None, 1000)

    assert error.value.status_code == 413
    assert error.value.details == {"max_bytes": 1000}


def test_file_exactly_at_the_limit_is_accepted() -> None:
    assert inspect_upload(io.BytesIO(b"a" * 1000), "a.txt", None, 1000).size_bytes == 1000
