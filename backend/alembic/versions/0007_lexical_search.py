"""Add lexical (full-text) search to document chunks.

- `user_id` on every chunk, a database-enforced copy of the document's owner (composite foreign
  key, like `chunk_embeddings`). A trigger fills it from the document, so no insert can forget it
  and none can carry another user's id.
- `search_vector`: a generated tsvector built with the `simple` configuration (lower-casing and
  tokenising only; PostgreSQL has no Bengali dictionary, so there is no stemming or stop-word
  removal). The original chunk text is never changed.
- A composite GIN index on `(user_id, search_vector)` (extension `btree_gin`): the owner is part
  of the index scan, so a lexical query reads only the caller's postings.

The upgrade refuses to run if the database locale cannot tokenise Bengali correctly.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BENGALI_CHECK = """
DO $$
BEGIN
    IF array_length(tsvector_to_array(to_tsvector('simple',
        E'\u0987\u09b2\u09bf\u09b6 \u09a7\u09b0\u09be \u0995\u09cd\u09b7\u09c1\u09a6\u09cd\u09b0')), 1)
       IS DISTINCT FROM 3 THEN
        RAISE EXCEPTION 'This database locale does not tokenise Bengali words correctly '
            '(full-text search would split words at vowel signs). Use a UTF-8 locale such as '
            'en_US.UTF-8 or C.UTF-8 for the database.';
    END IF;
END $$;
"""

OWNER_FUNCTION = """
CREATE FUNCTION set_chunk_owner() RETURNS trigger AS $$
BEGIN
    IF NEW.user_id IS NULL THEN
        SELECT user_id INTO NEW.user_id FROM documents WHERE id = NEW.document_id;
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
    op.execute(BENGALI_CHECK)

    op.add_column("document_chunks", sa.Column("user_id", postgresql.UUID(as_uuid=True)))
    op.execute(
        "UPDATE document_chunks c SET user_id = d.user_id FROM documents d WHERE d.id = c.document_id"
    )
    op.alter_column("document_chunks", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_document_chunks_owner",
        "document_chunks",
        "documents",
        ["document_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )
    op.execute(OWNER_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_document_chunks_owner BEFORE INSERT ON document_chunks "
        "FOR EACH ROW EXECUTE FUNCTION set_chunk_owner()"
    )

    op.add_column(
        "document_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', text)", persisted=True),
        ),
    )
    op.create_index(
        "ix_document_chunks_search",
        "document_chunks",
        ["user_id", "search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_search", table_name="document_chunks")
    op.drop_column("document_chunks", "search_vector")
    op.execute("DROP TRIGGER trg_document_chunks_owner ON document_chunks")
    op.execute("DROP FUNCTION set_chunk_owner()")
    op.drop_constraint("fk_document_chunks_owner", "document_chunks", type_="foreignkey")
    op.drop_column("document_chunks", "user_id")
