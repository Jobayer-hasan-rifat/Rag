import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import (
    ROLE_ASSISTANT,
    ROLE_USER,
    STATUS_ANSWERED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    Conversation,
    Message,
)


@dataclass(frozen=True)
class Turn:
    """A completed exchange: the question and the answer stored for it."""

    question: Message
    answer: Message


class ConversationRepository:
    """Every query takes the owner's id: nothing here can reach another user's rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- conversations -----------------------------------------------------------------

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Conversation)
                    .where(Conversation.user_id == user_id)
                )
            ).scalar_one()
        )

    async def create(
        self, user_id: uuid.UUID, title: str, *, customised: bool = False
    ) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title, title_customised=customised)
        self._session.add(conversation)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def get_owned(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, *, lock: bool = False
    ) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_page(
        self, user_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[Conversation, int]], int]:
        total = await self.count_for_user(user_id)
        conversations = (
            (
                await self._session.execute(
                    select(Conversation)
                    .where(Conversation.user_id == user_id)
                    .order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        counts: dict[uuid.UUID, int] = {}
        if conversations:  # count only the conversations on this page
            rows = await self._session.execute(
                select(Message.conversation_id, func.count())
                .where(
                    Message.user_id == user_id,
                    Message.conversation_id.in_([c.id for c in conversations]),
                )
                .group_by(Message.conversation_id)
            )
            counts = {row[0]: int(row[1]) for row in rows.all()}
        return [(c, counts.get(c.id, 0)) for c in conversations], total

    async def lock_user(self, user_id: uuid.UUID) -> None:
        """Serialise per-user limit checks (held until the transaction ends)."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:u, 0))"), {"u": str(user_id)}
        )

    async def message_count(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == conversation_id, Message.user_id == user_id)
                )
            ).scalar_one()
        )

    async def rename(self, conversation: Conversation, title: str) -> None:
        conversation.title = title
        conversation.title_customised = True
        await self._session.flush()
        await self._session.refresh(conversation)  # updated_at is set by the database

    async def delete(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
        """Hard delete; messages go with it (database cascade)."""
        result = await self._session.execute(
            delete(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def allocate_sequence(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> int:
        """The next message number, taken under the conversation's row lock."""
        result = await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .values(next_sequence=Conversation.next_sequence + 1, last_activity_at=func.now())
            .returning(Conversation.next_sequence - 1)
        )
        return int(result.scalar_one())

    async def set_default_title(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, title: str
    ) -> None:
        await self._session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.title_customised.is_(False),
            )
            .values(title=title)
        )

    # --- messages ----------------------------------------------------------------------

    async def add_question(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        sequence: int,
        content: str,
        scope: dict[str, Any],
        idempotency_key: str | None,
        request_hash: str | None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            user_id=user_id,
            sequence=sequence,
            role=ROLE_USER,
            status=STATUS_PROCESSING,
            content=content,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def add_answer(
        self,
        *,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        question_id: uuid.UUID,
        sequence: int,
        content: str,
        citations: list[dict[str, Any]],
        meta: dict[str, Any],
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            user_id=user_id,
            sequence=sequence,
            role=ROLE_ASSISTANT,
            status=STATUS_COMPLETE,
            content=content,
            reply_to_id=question_id,
            citations=citations,
            meta=meta,
        )
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def get_by_key(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, key: str
    ) -> Message | None:
        return (
            await self._session.execute(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.user_id == user_id,
                    Message.idempotency_key == key,
                )
            )
        ).scalar_one_or_none()

    async def answer_of(self, question_id: uuid.UUID) -> Message | None:
        return (
            await self._session.execute(select(Message).where(Message.reply_to_id == question_id))
        ).scalar_one_or_none()

    async def fail_stale(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, stale_seconds: int
    ) -> int:
        """Questions still `processing` after the stale limit were interrupted: mark them failed."""
        result = await self._session.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.user_id == user_id,
                Message.role == ROLE_USER,
                Message.status == STATUS_PROCESSING,
                Message.updated_at < func.now() - timedelta(seconds=stale_seconds),  # database time
            )
            .values(status=STATUS_FAILED, failure_reason="interrupted", updated_at=func.now())
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def resume(self, message: Message) -> int | None:
        """Put a failed question back in flight (a retry) and return its new attempt number, or
        None if it is not failed any more."""
        result = await self._session.execute(
            update(Message)
            .where(Message.id == message.id, Message.status == STATUS_FAILED)
            .values(
                status=STATUS_PROCESSING,
                failure_reason=None,
                attempts=Message.attempts + 1,
                updated_at=func.now(),
            )
            .returning(Message.attempts)
        )
        row = result.first()
        return int(row[0]) if row else None

    async def mark_failed(self, message_id: uuid.UUID, attempt: int, reason: str) -> None:
        await self._session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == STATUS_PROCESSING,
                Message.attempts == attempt,
            )
            .values(status=STATUS_FAILED, failure_reason=reason, updated_at=func.now())
        )

    async def mark_answered(self, message_id: uuid.UUID, attempt: int) -> bool:
        result = await self._session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == STATUS_PROCESSING,
                Message.attempts == attempt,
            )
            .values(status=STATUS_ANSWERED, updated_at=func.now())
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def messages_page(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        descending: bool,
    ) -> tuple[list[Message], int]:
        total = await self.message_count(user_id, conversation_id)
        order = Message.sequence.desc() if descending else Message.sequence.asc()
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.user_id == user_id)
            .order_by(order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def recent_turns(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int, *, before: int
    ) -> list[Turn]:
        """The latest completed exchanges before sequence `before`, oldest first. Only answered
        questions with a stored answer count: failed or in-flight ones are not history."""
        if limit <= 0:
            return []
        questions = (
            (
                await self._session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.user_id == user_id,
                        Message.role == ROLE_USER,
                        Message.status == STATUS_ANSWERED,
                        Message.sequence < before,
                    )
                    .order_by(Message.sequence.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not questions:
            return []
        answers = (
            (
                await self._session.execute(
                    select(Message).where(
                        Message.reply_to_id.in_([q.id for q in questions]),
                        Message.user_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_question = {a.reply_to_id: a for a in answers}
        turns = [Turn(q, by_question[q.id]) for q in questions if q.id in by_question]
        turns.reverse()
        return turns

    # --- document deletion -------------------------------------------------------------

    async def scrub_document_citations(self, user_id: uuid.UUID, document_id: uuid.UUID) -> int:
        """Reduce stored citations of a deleted document to bare identifiers (label, ids,
        `removed`): its name and heading are not kept after the document is gone."""
        result = await self._session.execute(
            text("""
                UPDATE messages SET citations = (
                    SELECT COALESCE(jsonb_agg(
                        CASE WHEN elem->>'document_id' = :doc
                             THEN jsonb_build_object('label', elem->'label',
                                                     'document_id', elem->'document_id',
                                                     'chunk_id', elem->'chunk_id',
                                                     'removed', true)
                             ELSE elem END ORDER BY ord), '[]'::jsonb)
                    FROM jsonb_array_elements(citations) WITH ORDINALITY AS t(elem, ord))
                WHERE user_id = CAST(:user AS uuid) AND role = 'assistant'
                  AND citations @> jsonb_build_array(
                        jsonb_build_object('document_id', CAST(:doc AS text)))
                """),
            {"user": str(user_id), "doc": str(document_id)},
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
