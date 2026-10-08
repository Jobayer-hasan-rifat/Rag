import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentSection(Base):
    """Normalised text of one page (PDF) or heading-delimited section (DOCX, Markdown, text).

    Sections are the unit later phases chunk and embed, so each one keeps its source
    location (`page_number`, `heading`).
    """

    __tablename__ = "document_sections"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_sections_document_ordinal"),
        # target of the composite foreign key from document_chunks
        UniqueConstraint("id", "document_id", name="uq_document_sections_id_document"),
        CheckConstraint("kind IN ('page', 'section', 'body')", name="kind_valid"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("char_count = char_length(text)", name="char_count_matches"),
        CheckConstraint("page_number IS NULL OR page_number >= 1", name="page_number_positive"),
        CheckConstraint("(kind = 'page') = (page_number IS NOT NULL)", name="page_number_iff_page"),
        CheckConstraint(
            "heading_level IS NULL OR heading_level BETWEEN 1 AND 9", name="heading_level_range"
        ),
        Index("ix_document_sections_document_page", "document_id", "page_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    heading_level: Mapped[int | None] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
