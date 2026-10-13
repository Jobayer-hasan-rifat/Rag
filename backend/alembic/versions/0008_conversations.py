"""Add persistent conversations and messages.

- `conversations` belong to one user (cascade with the user) and expose `UNIQUE (id, user_id)`.
- `messages` carry `user_id` as a database-enforced copy of the conversation's owner (composite
  foreign key), so a message can never be attached to another user's conversation. A message
  cascades with its conversation.
- A question is a `user` message and its answer an `assistant` message with `reply_to_id`
  (unique): the database allows at most one answer per question, and at most one in-flight
  question per conversation (partial unique index), which makes concurrent submissions
  deterministic. An idempotency key is unique per conversation.
- `citations` hold references and metadata only; no retrieved text, prompt or provider payload is
  stored.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "title_customised", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("next_sequence", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _timestamp("last_activity_at"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name=op.f("ck_conversations_title_length")
        ),
        sa.CheckConstraint(
            "title !~ '[[:cntrl:]]'", name=op.f("ck_conversations_title_no_control_characters")
        ),
        sa.CheckConstraint(
            "next_sequence >= 1", name=op.f("ck_conversations_next_sequence_positive")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("id", "user_id", name="uq_conversations_id_user_id"),
    )
    op.create_index(
        "ix_conversations_user_activity",
        "conversations",
        ["user_id", sa.text("last_activity_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "scope", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "citations", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role_valid")),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_messages_sequence_positive")),
        sa.CheckConstraint("attempts >= 1", name=op.f("ck_messages_attempts_positive")),
        sa.CheckConstraint(
            "(role = 'user' AND status IN ('processing', 'answered', 'failed') "
            "AND reply_to_id IS NULL) OR "
            "(role = 'assistant' AND status = 'complete' AND reply_to_id IS NOT NULL)",
            name=op.f("ck_messages_role_status_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)",
            name=op.f("ck_messages_failure_reason_iff_failed"),
        ),
        sa.CheckConstraint(
            "role = 'user' OR (idempotency_key IS NULL AND request_hash IS NULL)",
            name=op.f("ck_messages_idempotency_only_on_questions"),
        ),
        sa.CheckConstraint(
            "(idempotency_key IS NULL) = (request_hash IS NULL)",
            name=op.f("ck_messages_idempotency_pair"),
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 20000", name=op.f("ck_messages_content_length")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(citations) = 'array' AND jsonb_array_length(citations) <= 50",
            name=op.f("ck_messages_citations_bounded_array"),
        ),
        sa.CheckConstraint(
            "role = 'assistant' OR citations = '[]'::jsonb",
            name=op.f("ck_messages_citations_on_answers"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(meta) = 'object' AND jsonb_typeof(scope) = 'object'",
            name=op.f("ck_messages_json_objects"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_messages_conversation_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_messages_reply_to",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_sequence"
        ),
        sa.UniqueConstraint("id", "conversation_id", name="uq_messages_id_conversation_id"),
        sa.UniqueConstraint("reply_to_id", name="uq_messages_reply_to_id"),
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index(
        "uq_messages_one_in_flight",
        "messages",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("role = 'user' AND status = 'processing'"),
    )
    op.create_index(
        "uq_messages_idempotency_key",
        "messages",
        ["conversation_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_messages_idempotency_key", table_name="messages")
    op.drop_index("uq_messages_one_in_flight", table_name="messages")
    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_user_activity", table_name="conversations")
    op.drop_table("conversations")
