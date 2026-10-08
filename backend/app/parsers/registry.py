from app.core.documents.processing import FailureReason, ProcessingFailure
from app.core.documents.types import DocumentType
from app.parsers.base import DocumentExtractor
from app.parsers.docx_parser import DOCXExtractor
from app.parsers.pdf_parser import PDFExtractor
from app.parsers.text_parser import MarkdownExtractor, TextExtractor

_EXTRACTORS: dict[DocumentType, DocumentExtractor] = {
    DocumentType.PDF: PDFExtractor(),
    DocumentType.DOCX: DOCXExtractor(),
    DocumentType.TXT: TextExtractor(),
    DocumentType.MD: MarkdownExtractor(),
}


def get_extractor(file_type: str) -> DocumentExtractor:
    try:
        return _EXTRACTORS[DocumentType(file_type)]
    except (ValueError, KeyError):
        raise ProcessingFailure(FailureReason.UNSUPPORTED_FORMAT) from None
