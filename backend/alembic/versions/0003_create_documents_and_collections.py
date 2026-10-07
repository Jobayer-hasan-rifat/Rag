"""Create collections, documents and document_collections.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _uuid_pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "collections",
        _uuid_pk(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 255", name=op.f("ck_collections_name_length")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_collections_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collections")),
        sa.UniqueConstraint("id", "user_id", name="uq_collections_id_user_id"),
    )
    op.create_index("ix_collections_user_id", "collections", ["user_id"])
    op.create_index(
        "uq_collections_user_name",
        "collections",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=10), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("file_type IN ('pdf', 'docx', 'txt', 'md')", name=op.f("ck_documents_file_type_valid")),
        sa.CheckConstraint(
            "status IN ('pending', 'parsing', 'chunking', 'embedding', 'indexing', 'ready', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
        sa.CheckConstraint("file_size > 0", name=op.f("ck_documents_file_size_positive")),
        sa.CheckConstraint("char_length(filename) BETWEEN 1 AND 255", name=op.f("ck_documents_filename_length")),
        sa.CheckConstraint(
            "status = 'failed' OR error_message IS NULL", name=op.f("ck_documents_error_only_when_failed")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_documents_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        sa.UniqueConstraint("user_id", "checksum_sha256", name="uq_documents_user_checksum"),
        sa.UniqueConstraint("id", "user_id", name="uq_documents_id_user_id"),
    )
    op.create_index(
        "ix_documents_user_created",
        "documents",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_collections",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["documents.id", "documents.user_id"],
            name="fk_document_collections_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id", "user_id"],
            ["collections.id", "collections.user_id"],
            name="fk_document_collections_collection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "collection_id", name=op.f("pk_document_collections")),
    )
    op.create_index(
        "ix_document_collections_collection_id", "document_collections", ["collection_id"]
    )


def downgrade() -> None:
    op.drop_table("document_collections")
    op.drop_table("documents")
    op.drop_table("collections")
