"""Add embedding configurations, chunk embeddings and embedding state on documents.

Vectors live in `chunk_embeddings` (pgvector, cosine distance), one row per chunk and embedding
configuration, so a model change never mixes incompatible vectors: the old rows stay readable
until the document is re-embedded under the new configuration.

Documents that Phase 5 left in `chunked` simply become eligible for embedding; the recovery sweep
queues them.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VECTOR_DIMENSION = 384  # the width is part of the schema; see app/embeddings/constants.py


def upgrade() -> None:
    op.create_table(
        "embedding_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.String(length=255), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=20), nullable=False),
        sa.Column("normalized", sa.Boolean(), nullable=False),
        sa.Column("query_prefix", sa.Text(), server_default="", nullable=False),
        sa.Column("passage_prefix", sa.Text(), server_default="", nullable=False),
        sa.Column("max_seq_length", sa.Integer(), nullable=False),
        sa.Column("include_heading", sa.Boolean(), nullable=False),
        sa.Column("preprocessing_version", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("dimension > 0", name=op.f("ck_embedding_configs_dimension_positive")),
        sa.CheckConstraint(
            "max_seq_length > 0", name=op.f("ck_embedding_configs_max_seq_length_positive")
        ),
        sa.CheckConstraint("metric IN ('cosine')", name=op.f("ck_embedding_configs_metric_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_configs")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_embedding_configs_fingerprint")),
    )

    op.create_unique_constraint(
        "uq_document_chunks_id_document", "document_chunks", ["id", "document_id"]
    )

    op.add_column(
        "documents",
        sa.Column("embedding_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("documents", sa.Column("embedding_started_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("embedding_updated_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("embedding_completed_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("embedded_chunk_count", sa.Integer()))
    op.add_column(
        "documents", sa.Column("embedding_config_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_documents_embedding_cfg",
        "documents",
        "embedding_configs",
        ["embedding_config_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_documents_embedding_attempts_non_negative"),
        "documents",
        "embedding_attempts >= 0",
    )
    op.create_check_constraint(
        op.f("ck_documents_embedded_chunk_count_non_negative"),
        "documents",
        "embedded_chunk_count IS NULL OR embedded_chunk_count >= 0",
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", Vector(VECTOR_DIMENSION), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(text_sha256) = 64", name=op.f("ck_chunk_embeddings_text_sha256_length")
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_id"],
            ["document_chunks.id", "document_chunks.document_id"],
            name="fk_chunk_embeddings_chunk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_chunk_embeddings_document_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["embedding_configs.id"],
            name="fk_chunk_embeddings_config",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint("chunk_id", "config_id", name="uq_chunk_embeddings_chunk_config"),
    )
    op.create_index("ix_chunk_embeddings_document_id", "chunk_embeddings", ["document_id"])
    op.create_index("ix_chunk_embeddings_user_config", "chunk_embeddings", ["user_id", "config_id"])
    op.create_index("ix_chunk_embeddings_config_id", "chunk_embeddings", ["config_id"])


def downgrade() -> None:
    op.drop_table("chunk_embeddings")
    # vectors are gone, so documents that were embedding or searchable are merely chunked again
    op.execute("UPDATE documents SET status = 'chunked' WHERE status IN ('embedding', 'ready')")
    op.drop_constraint(
        op.f("ck_documents_embedded_chunk_count_non_negative"), "documents", type_="check"
    )
    op.drop_constraint(op.f("ck_documents_embedding_attempts_non_negative"), "documents", type_="check")
    op.drop_constraint("fk_documents_embedding_cfg", "documents", type_="foreignkey")
    for column in (
        "embedding_config_id",
        "embedded_chunk_count",
        "embedding_completed_at",
        "embedding_updated_at",
        "embedding_started_at",
        "embedding_attempts",
    ):
        op.drop_column("documents", column)
    op.drop_constraint("uq_document_chunks_id_document", "document_chunks", type_="unique")
    op.drop_table("embedding_configs")
