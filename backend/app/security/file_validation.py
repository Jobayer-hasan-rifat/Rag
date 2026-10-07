import codecs
import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

from app.core.documents.types import (
    EXTENSION_TO_TYPE,
    TYPE_INFO,
    UNSPECIFIED_CONTENT_TYPES,
    DocumentType,
)
from app.exceptions import (
    FileTooLargeError,
    InvalidFileError,
    UnsupportedFileTypeError,
)

MAX_FILENAME_LENGTH = 255
CHUNK_SIZE = 64 * 1024
_PDF_HEADER_WINDOW = 1024
_PDF_EOF_WINDOW = 4096
_MAX_ZIP_ENTRIES = 5000
_MAX_ZIP_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_MAX_ZIP_RATIO = 200
_DOCX_REQUIRED_PARTS = ("[Content_Types].xml", "word/document.xml")
_WHITESPACE = re.compile(r"\s+")
_MISMATCH = "File content does not match a supported document type"


@dataclass(frozen=True)
class InspectedFile:
    document_type: DocumentType
    content_type: str
    size_bytes: int
    sha256: str


def sanitize_filename(raw: str | None) -> str:
    """Reduce an untrusted client filename to a safe display name.

    The result is metadata only; it is never used to build a storage path.
    """
    if not raw:
        raise InvalidFileError("A file name is required")
    name = unicodedata.normalize("NFC", raw)
    name = re.split(r"[\\/]", name)[-1]  # drop any directory part, POSIX or Windows style
    name = "".join(char for char in name if not unicodedata.category(char).startswith("C"))
    name = _WHITESPACE.sub(" ", name).strip(" .")
    if name in {"", ".", ".."}:
        raise InvalidFileError("A valid file name is required")
    return _truncate_keeping_extension(name)


def _truncate_keeping_extension(name: str) -> str:
    if len(name) <= MAX_FILENAME_LENGTH:
        return name
    stem, dot, extension = name.rpartition(".")
    if not dot or len(extension) > 10:
        return name[:MAX_FILENAME_LENGTH]
    return stem[: MAX_FILENAME_LENGTH - len(extension) - 1] + "." + extension


def document_type_for_filename(filename: str) -> DocumentType:
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    doc_type = EXTENSION_TO_TYPE.get(extension)
    if doc_type is None:
        raise UnsupportedFileTypeError("Unsupported file type; allowed: PDF, DOCX, TXT, MD")
    return doc_type


def inspect_upload(
    file: BinaryIO, filename: str, declared_content_type: str | None, max_bytes: int
) -> InspectedFile:
    """Validate an upload's type, size and structure and compute its checksum.

    Never parses document contents; it checks signatures and container structure only.
    """
    doc_type = document_type_for_filename(filename)
    _check_declared_type(doc_type, declared_content_type)
    size, sha256 = _measure(
        file, max_bytes, text_check=doc_type in {DocumentType.TXT, DocumentType.MD}
    )
    if size == 0:
        raise InvalidFileError("The uploaded file is empty")
    if doc_type is DocumentType.PDF:
        _check_pdf(file, size)
    elif doc_type is DocumentType.DOCX:
        _check_docx(file)
    return InspectedFile(
        document_type=doc_type,
        content_type=TYPE_INFO[doc_type].canonical_content_type,
        size_bytes=size,
        sha256=sha256,
    )


def _check_declared_type(doc_type: DocumentType, declared: str | None) -> None:
    media_type = (declared or "").split(";", 1)[0].strip().lower()
    if media_type in UNSPECIFIED_CONTENT_TYPES:
        return
    if media_type not in TYPE_INFO[doc_type].accepted_content_types:
        raise UnsupportedFileTypeError("Declared content type does not match the file extension")


def _measure(file: BinaryIO, max_bytes: int, *, text_check: bool) -> tuple[int, str]:
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")() if text_check else None
    size = 0
    file.seek(0)
    while chunk := file.read(CHUNK_SIZE):
        size += len(chunk)
        if size > max_bytes:
            raise FileTooLargeError(max_bytes)
        digest.update(chunk)
        if decoder is not None:
            if b"\x00" in chunk:
                raise UnsupportedFileTypeError(_MISMATCH)
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                raise UnsupportedFileTypeError(_MISMATCH) from None
    if decoder is not None:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise UnsupportedFileTypeError(_MISMATCH) from None
    return size, digest.hexdigest()


def _check_pdf(file: BinaryIO, size: int) -> None:
    file.seek(0)
    head = file.read(_PDF_HEADER_WINDOW)
    file.seek(max(size - _PDF_EOF_WINDOW, 0))
    tail = file.read(_PDF_EOF_WINDOW)
    if b"%PDF-" not in head or b"%%EOF" not in tail:
        raise UnsupportedFileTypeError(_MISMATCH)


def _check_docx(file: BinaryIO) -> None:
    file.seek(0)
    try:
        with zipfile.ZipFile(file) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ZIP_ENTRIES:
                raise UnsupportedFileTypeError(_MISMATCH)
            uncompressed = sum(entry.file_size for entry in entries)
            compressed = sum(entry.compress_size for entry in entries) or 1
            if (
                uncompressed > _MAX_ZIP_UNCOMPRESSED_BYTES
                or uncompressed / compressed > _MAX_ZIP_RATIO
            ):
                raise UnsupportedFileTypeError(_MISMATCH)
            names = {entry.filename for entry in entries}
            if not all(part in names for part in _DOCX_REQUIRED_PARTS):
                raise UnsupportedFileTypeError(_MISMATCH)
    except zipfile.BadZipFile:
        raise UnsupportedFileTypeError(_MISMATCH) from None
    finally:
        file.seek(0)
