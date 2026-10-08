"""Add processing columns to documents and create document_sections.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("failure_reason", sa.String(length=50), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("character_count", sa.BigInteger(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("processing_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column(
            "processing_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_documents_failure_only_when_failed"),
        "documents",
        "status = 'failed' OR failure_reason IS NULL",
    )
    op.create_check_constraint(
        op.f("ck_documents_attempts_non_negative"), "documents", "processing_attempts >= 0"
    )
    op.create_check_constraint(
        op.f("ck_documents_page_count_non_negative"),
        "documents",
        "page_count IS NULL OR page_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_documents_character_count_non_negative"),
        "documents",
        "character_count IS NULL OR character_count >= 0",
    )

    op.create_table(
        "document_sections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("heading_level", sa.SmallInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('page', 'section', 'body')", name=op.f("ck_document_sections_kind_valid")
        ),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_document_sections_ordinal_non_negative")),
        sa.CheckConstraint(
            "char_count = char_length(text)", name=op.f("ck_document_sections_char_count_matches")
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name=op.f("ck_document_sections_page_number_positive"),
        ),
        sa.CheckConstraint(
            "(kind = 'page') = (page_number IS NOT NULL)",
            name=op.f("ck_document_sections_page_number_iff_page"),
        ),
        sa.CheckConstraint(
            "heading_level IS NULL OR heading_level BETWEEN 1 AND 9",
            name=op.f("ck_document_sections_heading_level_range"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_sections_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_sections")),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_document_sections_document_ordinal"),
    )
    op.create_index(
        "ix_document_sections_document_page", "document_sections", ["document_id", "page_number"]
    )


def downgrade() -> None:
    op.drop_table("document_sections")
    for constraint in (
        "ck_documents_character_count_non_negative",
        "ck_documents_page_count_non_negative",
        "ck_documents_attempts_non_negative",
        "ck_documents_failure_only_when_failed",
    ):
        op.drop_constraint(op.f(constraint), "documents", type_="check")
    for column in (
        "processing_metadata",
        "processing_attempts",
        "processing_completed_at",
        "processing_started_at",
        "character_count",
        "page_count",
        "failure_reason",
    ):
        op.drop_column("documents", column)
