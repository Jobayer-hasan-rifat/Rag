import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.embeddings.constants import VECTOR_DIMENSION


class ChunkEmbedding(Base):
    """The vector of one chunk under one embedding configuration."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "config_id", name="uq_chunk_embeddings_chunk_config"),
        # a vector can only point at a chunk of its own document...
        ForeignKeyConstraint(
            ["chunk_id", "document_id"],
            ["document_chunks.id", "document_chunks.document_id"],
            name="fk_chunk_embeddings_chunk",
            ondelete="CASCADE",
        ),
        # ...and carries the owner of that document, enforced by the database
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_chunk_embeddings_document_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint("char_length(text_sha256) = 64", name="text_sha256_length"),
        Index("ix_chunk_embeddings_document_id", "document_id"),
        Index("ix_chunk_embeddings_user_config", "user_id", "config_id"),
        Index("ix_chunk_embeddings_config_id", "config_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("embedding_configs.id", ondelete="RESTRICT", name="fk_chunk_embeddings_config"),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(VECTOR_DIMENSION), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    truncated: Mapped[bool] = mapped_column(
        Boolean, server_default=sql_text("false"), default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
