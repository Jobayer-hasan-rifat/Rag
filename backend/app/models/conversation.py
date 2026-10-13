import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

MAX_TITLE_CHARS = 200
MAX_MESSAGE_CHARS = 20_000
MAX_CITATIONS = 50

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
STATUS_PROCESSING = "processing"
STATUS_ANSWERED = "answered"
STATUS_FAILED = "failed"
STATUS_COMPLETE = "complete"


class Conversation(TimestampMixin, Base):
    """A user's conversation. Messages cascade with it; deleting it deletes them."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(f"char_length(title) BETWEEN 1 AND {MAX_TITLE_CHARS}", name="title_length"),
        CheckConstraint("title !~ '[[:cntrl:]]'", name="title_no_control_characters"),
        CheckConstraint("next_sequence >= 1", name="next_sequence_positive"),
        # Target of the composite foreign key that keeps a message with its conversation's owner.
        UniqueConstraint("id", "user_id", name="uq_conversations_id_user_id"),
        Index(
            "ix_conversations_user_activity",
            "user_id",
            text("last_activity_at DESC"),
            text("id DESC"),
        ),
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
    title: Mapped[str] = mapped_column(String(MAX_TITLE_CHARS), nullable=False)
    title_customised: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    # Next message number: allocated under the row lock, so numbering is gapless per conversation.
    next_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Message(TimestampMixin, Base):
    """One message. A question is a `user` message; its answer is the `assistant` message that
    points back at it with `reply_to_id` (unique, so a question has at most one answer)."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_messages_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["reply_to_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_messages_reply_to",
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_conversation_sequence"),
        UniqueConstraint("id", "conversation_id", name="uq_messages_id_conversation_id"),
        UniqueConstraint("reply_to_id", name="uq_messages_reply_to_id"),
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("attempts >= 1", name="attempts_positive"),
        CheckConstraint(
            "(role = 'user' AND status IN ('processing', 'answered', 'failed') "
            "AND reply_to_id IS NULL) OR "
            "(role = 'assistant' AND status = 'complete' AND reply_to_id IS NOT NULL)",
            name="role_status_consistent",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)", name="failure_reason_iff_failed"
        ),
        CheckConstraint(
            "role = 'user' OR (idempotency_key IS NULL AND request_hash IS NULL)",
            name="idempotency_only_on_questions",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL) = (request_hash IS NULL)", name="idempotency_pair"
        ),
        CheckConstraint(
            f"char_length(content) BETWEEN 1 AND {MAX_MESSAGE_CHARS}", name="content_length"
        ),
        CheckConstraint(
            "jsonb_typeof(citations) = 'array' "
            f"AND jsonb_array_length(citations) <= {MAX_CITATIONS}",
            name="citations_bounded_array",
        ),
        CheckConstraint(
            "role = 'assistant' OR citations = '[]'::jsonb", name="citations_on_answers"
        ),
        CheckConstraint(
            "jsonb_typeof(meta) = 'object' AND jsonb_typeof(scope) = 'object'", name="json_objects"
        ),
        Index("ix_messages_user_id", "user_id"),
        Index(
            "uq_messages_one_in_flight",
            "conversation_id",
            unique=True,
            postgresql_where=text("role = 'user' AND status = 'processing'"),
        ),
        Index(
            "uq_messages_idempotency_key",
            "conversation_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    # Counts takeovers of this question; only the holder of the current attempt may finish it.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # The filters of this turn (not inherited by later turns) and bounded, safe diagnostics.
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # References only: no retrieved text is stored (an excerpt is read from the chunk when shown).
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
