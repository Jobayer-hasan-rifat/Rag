import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import ChunkRecord, EmbeddingRepository
from app.exceptions import (
    AppError,
    ConversationBusyError,
    ConversationLimitError,
    GenerationFailedError,
    GenerationUnavailableError,
    IdempotencyKeyReusedError,
    MessageInProgressError,
    NotFoundError,
    RagDisabledError,
)
from app.models.conversation import (
    MAX_MESSAGE_CHARS,
    STATUS_ANSWERED,
    STATUS_PROCESSING,
    Conversation,
    Message,
)
from app.models.user import User
from app.observability.logging import get_logger
from app.rag.history import HistoryTurn
from app.schemas.conversation import SendMessageRequest
from app.services.conversation_text import (
    DEFAULT_TITLE,
    default_title,
    explicit_title,
    request_fingerprint,
)
from app.services.rag_presenter import assistant_details, build_response, citation_reference
from app.services.rag_service import RagOutcome, RagService
from app.services.search_common import validate_scope

logger = get_logger("app.conversations")


@dataclass(frozen=True)
class ConversationView:
    conversation: Conversation
    message_count: int


@dataclass(frozen=True)
class MessageView:
    """A message plus what is currently readable of its citations (owner, ready, still there)."""

    message: Message
    chunks: dict[uuid.UUID, ChunkRecord]


@dataclass(frozen=True)
class SendResult:
    conversation: ConversationView
    question: MessageView
    answer: MessageView
    replayed: bool


def failure_reason_of(error: BaseException) -> str:
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, GenerationUnavailableError):
        return str(error.details.get("reason", "unavailable"))[:32]
    if isinstance(error, GenerationFailedError):
        return "invalid_output"
    if isinstance(error, NotFoundError):
        return "scope_unavailable"
    return "error"


class ConversationService:
    """Conversations around the Phase 9 RAG service: this class stores, orders and protects the
    messages; retrieval, context, generation and citation validation stay in `RagService`.

    Every query is scoped to the authenticated user, and a conversation that is missing or someone
    else's is the same 404. A question is persisted before generation (so it never vanishes) in a
    short transaction that is committed before the model is called; the answer is persisted in a
    second short transaction. No transaction or connection is open while retrieval or the model
    runs, and no answer is stored for a failure.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        conversations: ConversationRepository,
        documents: DocumentRepository,
        collections: CollectionRepository,
        embeddings: EmbeddingRepository,
        rag: RagService | None,
        settings: Settings,
    ) -> None:
        self._session = session
        self._conversations = conversations
        self._documents = documents
        self._collections = collections
        self._embeddings = embeddings
        self._rag = rag
        self._settings = settings

    # --- management --------------------------------------------------------------------

    async def create(self, actor: User, title: str | None) -> ConversationView:
        user_id = actor.id
        await self._conversations.lock_user(user_id)  # the cap check and insert are one step
        if (
            await self._conversations.count_for_user(user_id)
            >= self._settings.conversation_max_per_user
        ):
            raise ConversationLimitError(
                f"You can have at most {self._settings.conversation_max_per_user} conversations"
            )
        conversation = await self._conversations.create(
            user_id,
            explicit_title(title) if title is not None else DEFAULT_TITLE,
            customised=title is not None,
        )
        await self._session.commit()
        logger.info(
            "conversation created",
            extra={"user_id": str(user_id), "conversation_id": str(conversation.id)},
        )
        return ConversationView(conversation, 0)

    async def list_page(
        self, actor: User, *, page: int, page_size: int
    ) -> tuple[list[ConversationView], int]:
        rows, total = await self._conversations.list_page(actor.id, page=page, page_size=page_size)
        return [ConversationView(c, n) for c, n in rows], total

    async def _owned(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self._conversations.get_owned(user_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    async def get(
        self,
        actor: User,
        conversation_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        descending: bool,
    ) -> tuple[ConversationView, list[MessageView], int]:
        user_id = actor.id
        conversation = await self._owned(user_id, conversation_id)
        messages, total = await self._conversations.messages_page(
            user_id, conversation_id, page=page, page_size=page_size, descending=descending
        )
        return (
            ConversationView(conversation, total),
            await self._views(user_id, messages),
            total,
        )

    async def rename(self, actor: User, conversation_id: uuid.UUID, title: str) -> ConversationView:
        user_id = actor.id
        conversation = await self._owned(user_id, conversation_id)
        await self._conversations.rename(conversation, explicit_title(title))
        count = await self._conversations.message_count(user_id, conversation_id)
        await self._session.commit()
        logger.info(
            "conversation renamed",
            extra={"user_id": str(user_id), "conversation_id": str(conversation_id)},
        )
        return ConversationView(conversation, count)

    async def delete(self, actor: User, conversation_id: uuid.UUID) -> None:
        user_id = actor.id
        if not await self._conversations.delete(user_id, conversation_id):
            raise NotFoundError("Conversation not found")
        await self._session.commit()
        logger.info(
            "conversation deleted",
            extra={"user_id": str(user_id), "conversation_id": str(conversation_id)},
        )

    async def _views(self, user_id: uuid.UUID, messages: list[Message]) -> list[MessageView]:
        ids = {
            uuid.UUID(c["chunk_id"]) for m in messages for c in m.citations if not c.get("removed")
        }
        chunks = await self._embeddings.hydrate(user_id=user_id, chunk_ids=list(ids)) if ids else {}
        return [MessageView(m, chunks) for m in messages]

    # --- a turn --------------------------------------------------------------------------

    async def send_message(
        self,
        actor: User,
        conversation_id: uuid.UUID,
        request: SendMessageRequest,
        idempotency_key: str | None,
    ) -> SendResult:
        if self._rag is None:
            raise RagDisabledError()
        user_id = actor.id
        started = time.monotonic()
        text = self._rag.validate(
            request.content,
            max_sources=request.max_sources,
            max_answer_tokens=request.max_answer_tokens,
            rerank=request.rerank,
        )
        scope: dict[str, Any] = {
            "document_ids": sorted(str(i) for i in request.document_ids or []),
            "collection_id": str(request.collection_id) if request.collection_id else None,
            "retrieval": request.retrieval or self._settings.rag_retrieval_mode,
            "rerank": request.rerank,
            "max_sources": request.max_sources,
            "max_answer_tokens": request.max_answer_tokens,
        }
        fingerprint = request_fingerprint(text, scope) if idempotency_key else None
        if idempotency_key is not None:
            # a stored answer is returned as it is, even if a filtered document was deleted since
            replay = await self._stored_answer(
                user_id, conversation_id, idempotency_key, fingerprint
            )
            if replay is not None:
                return replay
        await validate_scope(
            self._documents, self._collections, user_id, request.document_ids, request.collection_id
        )

        # transaction A: store the question, take the history, commit
        opened = await self._open_turn(
            user_id, conversation_id, text, scope, idempotency_key, fingerprint
        )
        if isinstance(opened, SendResult):
            return opened
        question_id, sequence, attempt, history = opened
        await self._session.commit()

        # no transaction or connection is held from here until the answer is stored
        try:
            outcome = await self._rag.answer(
                actor,
                question=text,
                document_ids=request.document_ids,
                collection_id=request.collection_id,
                retrieval=request.retrieval,
                rerank=request.rerank,
                max_sources=request.max_sources,
                max_answer_tokens=request.max_answer_tokens,
                history=history,
            )
            shown = len(outcome.history.turns) if outcome.history else 0
            result = await self._store_answer(
                user_id, conversation_id, question_id, attempt, outcome, shown
            )
        except BaseException as error:
            await self._record_failure(user_id, conversation_id, question_id, attempt, error)
            if isinstance(error, AppError):
                error.details.setdefault("message_id", str(question_id))
            raise
        logger.info(
            "conversation message",
            extra={
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "sequence": sequence,
                "history_turns": shown,
                "answered": not outcome.insufficient,
                "replayed": False,
                "total_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return result

    async def _stored_answer(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        key: str,
        fingerprint: str | None,
    ) -> SendResult | None:
        """The stored result for an idempotency key that was already answered (read only)."""
        if await self._conversations.get_owned(user_id, conversation_id) is None:
            raise NotFoundError("Conversation not found")
        existing = await self._conversations.get_by_key(user_id, conversation_id, key)
        if existing is None:
            return None
        if existing.request_hash != fingerprint:
            raise IdempotencyKeyReusedError()
        if existing.status != STATUS_ANSWERED:
            return None
        return await self._replay(user_id, conversation_id, existing)

    async def _open_turn(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        text: str,
        scope: dict[str, Any],
        key: str | None,
        fingerprint: str | None,
    ) -> SendResult | tuple[uuid.UUID, int, int, list[HistoryTurn]]:
        repo = self._conversations
        settings = self._settings
        conversation = await repo.get_owned(user_id, conversation_id, lock=True)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        await repo.fail_stale(
            user_id, conversation_id, settings.conversation_processing_stale_seconds
        )
        existing = await repo.get_by_key(user_id, conversation_id, key) if key else None
        try:
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise IdempotencyKeyReusedError()
                if existing.status == STATUS_ANSWERED:
                    return await self._replay(user_id, conversation_id, existing)
                if existing.status == STATUS_PROCESSING:
                    raise MessageInProgressError()
                question_id, sequence = existing.id, existing.sequence
                resumed = await repo.resume(existing)
                if resumed is None:
                    raise MessageInProgressError()
                attempt = resumed
            else:
                if (
                    await repo.message_count(user_id, conversation_id) + 2
                    > settings.conversation_max_messages
                ):
                    raise ConversationLimitError(
                        "This conversation is full; start a new conversation"
                    )
                sequence = await repo.allocate_sequence(user_id, conversation_id)
                question = await repo.add_question(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    sequence=sequence,
                    content=text,
                    scope=scope,
                    idempotency_key=key,
                    request_hash=fingerprint,
                )
                question_id, attempt = question.id, 1
                if sequence == 1:
                    await repo.set_default_title(user_id, conversation_id, default_title(text))
            turns = await repo.recent_turns(
                user_id, conversation_id, settings.conversation_history_turns, before=sequence
            )
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()  # the one-in-flight index: another question is running
            raise ConversationBusyError() from None
        except AppError:
            await self._session.rollback()
            raise
        history = [
            HistoryTurn(
                t.question.content,
                None if t.answer.meta.get("insufficient_reason") else t.answer.content,
            )
            for t in turns
        ]
        return question_id, sequence, attempt, history

    async def _replay(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, question: Message
    ) -> SendResult:
        answer = await self._conversations.answer_of(question.id)
        if answer is None:  # answered without an answer row cannot happen (one transaction)
            raise MessageInProgressError()
        views = await self._views(user_id, [question, answer])
        conversation = await self._owned(user_id, conversation_id)
        count = await self._conversations.message_count(user_id, conversation_id)
        await self._session.commit()
        logger.info(
            "conversation message",
            extra={
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "sequence": question.sequence,
                "replayed": True,
            },
        )
        return SendResult(ConversationView(conversation, count), views[0], views[1], True)

    async def _record_failure(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        question_id: uuid.UUID,
        attempt: int,
        error: BaseException,
    ) -> None:
        reason = failure_reason_of(error)
        try:
            await self._session.rollback()
            await self._conversations.mark_failed(question_id, attempt, reason)
            await self._session.commit()
        except Exception:  # the stale sweep will mark it failed; never hide the original error
            logger.error("could not record a failed message", extra={"reason": reason})
            await self._session.rollback()
        logger.warning(
            "conversation message failed",
            extra={
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "reason": reason,
                "error_type": type(error).__name__,
            },
        )

    async def _store_answer(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        question_id: uuid.UUID,
        attempt: int,
        outcome: RagOutcome,
        history_turns: int,
    ) -> SendResult:
        repo = self._conversations
        response = build_response(outcome)
        meta = assistant_details(response)
        meta["insufficient_reason"] = outcome.insufficient_reason
        meta["history_turns"] = history_turns
        meta["retrieval_extended"] = outcome.retrieval_extended
        citations = [citation_reference(item) for item in outcome.citations]
        content = outcome.answer[:MAX_MESSAGE_CHARS]
        await self._session.rollback()
        conversation = await repo.get_owned(user_id, conversation_id, lock=True)
        if conversation is None:  # deleted while the model was working: nothing to store
            raise NotFoundError("Conversation not found")
        if not await repo.mark_answered(question_id, attempt):
            raise MessageInProgressError()  # a stale sweep or a retry took the question over
        # A cited document deleted while the model worked must not leave its name behind: the
        # documents are locked until this transaction ends, so a later delete scrubs this answer.
        cited = {uuid.UUID(c["document_id"]) for c in citations}
        alive = await self._documents.lock_existing_ids(user_id, cited)
        citations = [
            c if uuid.UUID(c["document_id"]) in alive else removed_reference(c) for c in citations
        ]
        sequence = await repo.allocate_sequence(user_id, conversation_id)
        try:
            answer = await repo.add_answer(
                conversation_id=conversation_id,
                user_id=user_id,
                question_id=question_id,
                sequence=sequence,
                content=content,
                citations=citations,
                meta=meta,
            )
            question = await self._session.get(Message, question_id)
            if question is None:  # cannot happen: the row was updated above
                raise MessageInProgressError()
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await repo.answer_of(question_id)
            if existing is None:
                raise  # a real constraint failure: the caller records the failure
            question = await self._session.get(Message, question_id)  # an answer already exists
            if question is None:
                raise
            answer = existing
        views = await self._views(user_id, [question, answer])
        conversation = await self._owned(user_id, conversation_id)
        count = await repo.message_count(user_id, conversation_id)
        return SendResult(ConversationView(conversation, count), views[0], views[1], False)


def removed_reference(reference: dict[str, Any]) -> dict[str, Any]:
    """A citation of a document that no longer exists: identifiers only."""
    return {
        "label": reference["label"],
        "document_id": reference["document_id"],
        "chunk_id": reference["chunk_id"],
        "removed": True,
    }
