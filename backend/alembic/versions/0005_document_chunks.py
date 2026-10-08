"""Add the chunked status, chunk columns and the document_chunks table.

Documents that Phase 4 left in `ready` only had their text extracted; `ready` now means
embedded and searchable, so they are sent back to `pending` and are re-processed (extracted
again, then chunked) by the recovery sweep.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'pending', 'parsing', 'chunking', 'embedding', 'indexing', 'ready', 'failed'"
NEW_STATUSES = "'pending', 'parsing', 'chunking', 'chunked', 'embedding', 'indexing', 'ready', 'failed'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"), "documents", f"status IN ({NEW_STATUSES})"
    )
    op.execute(
        "UPDATE documents SET status = 'pending', processing_attempts = 0, "
        "processing_completed_at = NULL WHERE status = 'ready'"
    )
    op.add_column("documents", sa.Column("chunk_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("chunking_version", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_chunk_count_non_negative"),
        "documents",
        "chunk_count IS NULL OR chunk_count >= 0",
    )
    op.create_unique_constraint(
        "uq_document_sections_id_document", "document_sections", ["id", "document_id"]
    )

    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunking_version", sa.String(length=20), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("overlap_chars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("heading_level", sa.SmallInteger(), nullable=True),
        sa.Column(
            "heading_path",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("chunk_index >= 0", name=op.f("ck_document_chunks_index_non_negative")),
        sa.CheckConstraint(
            "char_count = char_length(text)", name=op.f("ck_document_chunks_char_count_matches")
        ),
        sa.CheckConstraint("char_count > 0", name=op.f("ck_document_chunks_not_empty")),
        sa.CheckConstraint(
            "end_char > start_char AND start_char >= 0", name=op.f("ck_document_chunks_span_valid")
        ),
        sa.CheckConstraint(
            "end_char - start_char = char_count", name=op.f("ck_document_chunks_span_matches_length")
        ),
        sa.CheckConstraint(
            "overlap_chars >= 0 AND overlap_chars <= char_count",
            name=op.f("ck_document_chunks_overlap_valid"),
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name=op.f("ck_document_chunks_page_number_positive"),
        ),
        sa.CheckConstraint(
            "heading_level IS NULL OR heading_level BETWEEN 1 AND 9",
            name=op.f("ck_document_chunks_heading_level_range"),
        ),
        sa.ForeignKeyConstraint(
            ["section_id", "document_id"],
            ["document_sections.id", "document_sections.document_id"],
            name="fk_document_chunks_section",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "document_id", "chunking_version", "chunk_index", name="uq_document_chunks_order"
        ),
    )
    op.create_index("ix_document_chunks_section_id", "document_chunks", ["section_id"])
    op.create_index(
        "ix_document_chunks_document_page", "document_chunks", ["document_id", "page_number"]
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_constraint("uq_document_sections_id_document", "document_sections", type_="unique")
    op.drop_constraint(op.f("ck_documents_chunk_count_non_negative"), "documents", type_="check")
    op.drop_column("documents", "chunking_version")
    op.drop_column("documents", "chunk_count")
    op.execute("UPDATE documents SET status = 'ready' WHERE status = 'chunked'")
    op.execute("UPDATE documents SET status = 'parsing' WHERE status = 'chunking'")
    op.drop_constraint(op.f("ck_documents_status_valid"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_valid"), "documents", f"status IN ({OLD_STATUSES})"
    )
