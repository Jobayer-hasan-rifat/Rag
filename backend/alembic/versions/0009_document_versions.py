"""Add document versions.

Every existing `documents` row already is a complete version (its own file, content hash,
pipeline state, chunks and vectors), so the table is kept as the version table and a parent
`document_families` row becomes the stable identity of a document.

- `document_families` (id, owner, version counter). Backfill: one family per existing document
  with the same id, so every public document id, search filter and collection membership stays
  valid. The owner is enforced by a composite foreign key from `documents`.
- `documents` gains `family_id`, `version_number` (unique per family), `is_active` (the version
  retrieval may use: at most one per family, partial unique index) and `is_current` (the version
  the document is shown as: at most one per family). An active version is always current, and a
  trigger refuses to make a version active unless it is `ready`. Backfill: version 1, current,
  and active if `ready`.
- `document_collections` now references the family: membership belongs to the document, not to
  one version.

Downgrade is not lossless: it refuses to run while any document has more than one version
(delete the extra versions first), because the old schema cannot represent them.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVATE_FUNCTION = """
CREATE FUNCTION documents_require_ready_to_activate() RETURNS trigger AS $$
BEGIN
    IF NEW.is_active AND NEW.status <> 'ready'
       AND (TG_OP = 'INSERT' OR NOT OLD.is_active) THEN
        RAISE EXCEPTION 'only a ready document version can be made active'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql
"""


def _timestamp(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "document_families",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("next_version_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "next_version_number >= 1", name=op.f("ck_document_families_next_version_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_document_families_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_families")),
        sa.UniqueConstraint("id", "user_id", name="uq_document_families_id_user_id"),
    )
    op.create_index("ix_document_families_user_id", "document_families", ["user_id"])
    op.execute(
        "INSERT INTO document_families (id, user_id, next_version_number, created_at, updated_at) "
        "SELECT id, user_id, 2, created_at, updated_at FROM documents"
    )

    op.add_column("documents", sa.Column("family_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "documents",
        sa.Column("version_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute(
        "UPDATE documents SET family_id = id, is_current = true, is_active = (status = 'ready')"
    )
    op.alter_column("documents", "family_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_family_owner",
        "documents",
        "document_families",
        ["family_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_documents_family_version", "documents", ["family_id", "version_number"]
    )
    op.create_check_constraint(
        op.f("ck_documents_version_number_positive"), "documents", "version_number >= 1"
    )
    op.create_check_constraint(
        op.f("ck_documents_active_is_current"), "documents", "NOT is_active OR is_current"
    )
    op.create_index(
        "uq_documents_one_active",
        "documents",
        ["family_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "uq_documents_one_current",
        "documents",
        ["family_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_documents_user_active", "documents", ["user_id"], postgresql_where=sa.text("is_active")
    )
    op.create_index(
        "ix_documents_user_current_created",
        "documents",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("is_current"),
    )

    op.drop_constraint("fk_document_collections_document", "document_collections", type_="foreignkey")
    op.create_foreign_key(
        "fk_document_collections_document",
        "document_collections",
        "document_families",
        ["document_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )

    op.execute(ACTIVATE_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_documents_activate_ready BEFORE INSERT OR UPDATE OF is_active, status "
        "ON documents FOR EACH ROW EXECUTE FUNCTION documents_require_ready_to_activate()"
    )


def downgrade() -> None:
    extra = op.get_bind().execute(
        sa.text("SELECT count(*) FROM (SELECT 1 FROM documents GROUP BY family_id HAVING count(*) > 1) t")
    ).scalar_one()
    if extra:
        raise RuntimeError(
            f"{extra} document(s) have more than one version, which the previous schema cannot "
            "represent. Delete the extra versions (DELETE /api/v1/documents/{id}/versions/{version}) "
            "and run the downgrade again."
        )
    op.execute("DROP TRIGGER trg_documents_activate_ready ON documents")
    op.execute("DROP FUNCTION documents_require_ready_to_activate()")

    op.drop_constraint("fk_document_collections_document", "document_collections", type_="foreignkey")
    # membership named the family; the only remaining version row may carry another id
    op.execute(
        "UPDATE document_collections m SET document_id = d.id "
        "FROM documents d WHERE d.family_id = m.document_id AND d.id <> m.document_id"
    )
    op.create_foreign_key(
        "fk_document_collections_document",
        "document_collections",
        "documents",
        ["document_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )

    op.drop_index("ix_documents_user_current_created", table_name="documents")
    op.drop_index("ix_documents_user_active", table_name="documents")
    op.drop_index("uq_documents_one_current", table_name="documents")
    op.drop_index("uq_documents_one_active", table_name="documents")
    op.drop_constraint(op.f("ck_documents_active_is_current"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_version_number_positive"), "documents", type_="check")
    op.drop_constraint("uq_documents_family_version", "documents", type_="unique")
    op.drop_constraint("fk_documents_family_owner", "documents", type_="foreignkey")
    op.drop_column("documents", "is_current")
    op.drop_column("documents", "is_active")
    op.drop_column("documents", "version_number")
    op.drop_column("documents", "family_id")
    op.drop_index("ix_document_families_user_id", table_name="document_families")
    op.drop_table("document_families")
