from dataclasses import dataclass
from enum import StrEnum


class DocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MD = "md"


@dataclass(frozen=True)
class DocumentTypeInfo:
    extension: str
    canonical_content_type: str
    accepted_content_types: frozenset[str]


# Declared types are advisory: they must not contradict the file extension, but the
# authoritative check is the file's content (see app.security.file_validation).
UNSPECIFIED_CONTENT_TYPES = frozenset({"", "application/octet-stream"})

TYPE_INFO: dict[DocumentType, DocumentTypeInfo] = {
    DocumentType.PDF: DocumentTypeInfo(".pdf", "application/pdf", frozenset({"application/pdf"})),
    DocumentType.DOCX: DocumentTypeInfo(
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ),
    DocumentType.TXT: DocumentTypeInfo(
        ".txt", "text/plain; charset=utf-8", frozenset({"text/plain"})
    ),
    DocumentType.MD: DocumentTypeInfo(
        ".md",
        "text/markdown; charset=utf-8",
        frozenset({"text/markdown", "text/x-markdown", "text/plain"}),
    ),
}

EXTENSION_TO_TYPE: dict[str, DocumentType] = {
    info.extension: doc_type for doc_type, info in TYPE_INFO.items()
}
