import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentChunk(Base):
    """A retrievable passage. Always `section.text[start_char:end_char]` of its section.

    Chunks never span sections, so page/heading references are exact. Phase 6 adds the
    embedding column; retrieval will walk chunk -> section -> document.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunking_version", "chunk_index", name="uq_document_chunks_order"
        ),
        # the composite key guarantees a chunk's section belongs to the chunk's own document
        ForeignKeyConstraint(
            ["section_id", "document_id"],
            ["document_sections.id", "document_sections.document_id"],
            name="fk_document_chunks_section",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        # the owner copy: a trigger fills it from the document; this key makes any other value
        # impossible, so lexical search can filter on it directly
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_document_chunks_owner",
            ondelete="CASCADE",
        ),
        # lexical search: the owner is part of the index scan
        Index("ix_document_chunks_search", "user_id", "search_vector", postgresql_using="gin"),
        # target of the composite foreign key from chunk_embeddings (a vector's chunk and document)
        UniqueConstraint("id", "document_id", name="uq_document_chunks_id_document"),
        CheckConstraint("chunk_index >= 0", name="index_non_negative"),
        CheckConstraint("char_count = char_length(text)", name="char_count_matches"),
        CheckConstraint("char_count > 0", name="not_empty"),
        CheckConstraint("end_char > start_char AND start_char >= 0", name="span_valid"),
        CheckConstraint("end_char - start_char = char_count", name="span_matches_length"),
        CheckConstraint("overlap_chars >= 0 AND overlap_chars <= char_count", name="overlap_valid"),
        CheckConstraint("page_number IS NULL OR page_number >= 1", name="page_number_positive"),
        CheckConstraint(
            "heading_level IS NULL OR heading_level BETWEEN 1 AND 9", name="heading_level_range"
        ),
        Index("ix_document_chunks_section_id", "section_id"),
        Index("ix_document_chunks_document_page", "document_id", "page_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # set by the database from the document (trigger); never trusted from application code
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(20), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # tokens of `text` for full-text search; the text itself is never altered for this
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True)
    )
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    overlap_chars: Mapped[int] = mapped_column(
        Integer, server_default=sql_text("0"), default=0, nullable=False
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    heading_level: Mapped[int | None] = mapped_column(SmallInteger)
    heading_path: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=sql_text("'{}'"), default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
