import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("file_type IN ('pdf', 'docx', 'txt', 'md')", name="file_type_valid"),
        CheckConstraint(
            "status IN ('pending', 'parsing', 'chunking', 'chunked', 'embedding', 'indexing', "
            "'ready', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("file_size > 0", name="file_size_positive"),
        CheckConstraint("char_length(filename) BETWEEN 1 AND 255", name="filename_length"),
        CheckConstraint(
            "status = 'failed' OR error_message IS NULL", name="error_only_when_failed"
        ),
        CheckConstraint(
            "status = 'failed' OR failure_reason IS NULL", name="failure_only_when_failed"
        ),
        CheckConstraint("processing_attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("chunk_count IS NULL OR chunk_count >= 0", name="chunk_count_non_negative"),
        CheckConstraint("page_count IS NULL OR page_count >= 0", name="page_count_non_negative"),
        CheckConstraint(
            "character_count IS NULL OR character_count >= 0", name="character_count_non_negative"
        ),
        UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        UniqueConstraint("user_id", "checksum_sha256", name="uq_documents_user_checksum"),
        UniqueConstraint("id", "user_id", name="uq_documents_id_user_id"),
        Index("ix_documents_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'pending'"), default="pending", nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(String(50))
    page_count: Mapped[int | None] = mapped_column(Integer)
    character_count: Mapped[int | None] = mapped_column(BigInteger)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    chunking_version: Mapped[str | None] = mapped_column(String(20))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_attempts: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )
    processing_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict, nullable=False
    )


Index("ix_documents_user_created", Document.user_id, Document.created_at.desc(), Document.id.desc())


class DocumentCollection(Base):
    """Membership link. Composite foreign keys force document and collection to share an owner."""

    __tablename__ = "document_collections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_document_collections_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["collection_id", "user_id"],
            ["collections.id", "collections.user_id"],
            name="fk_document_collections_collection",
            ondelete="CASCADE",
        ),
        Index("ix_document_collections_collection_id", "collection_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    collection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
