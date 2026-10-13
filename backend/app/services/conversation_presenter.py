"""Conversation and message responses. Citation excerpts are read from the chunk when a message
is shown (only if it still exists and is the caller's), never from stored text."""

import uuid

from app.rag.grounding import excerpt
from app.schemas.conversation import ConversationResponse, MessageCitation, MessageResponse
from app.services.conversation_service import ConversationView, MessageView


def conversation_response(view: ConversationView) -> ConversationResponse:
    c = view.conversation
    return ConversationResponse(
        id=c.id,
        title=c.title,
        message_count=view.message_count,
        created_at=c.created_at,
        updated_at=c.updated_at,
        last_activity_at=c.last_activity_at,
    )


def _citation(stored: dict, view: MessageView) -> MessageCitation:  # type: ignore[type-arg]
    chunk = view.chunks.get(uuid.UUID(stored["chunk_id"]))
    shown, cut = excerpt(chunk.text) if chunk is not None else (None, None)
    return MessageCitation(
        label=stored["label"],
        document_id=stored["document_id"],
        chunk_id=stored["chunk_id"],
        document_name=stored.get("document_name"),
        chunk_index=stored.get("chunk_index"),
        section_ordinal=stored.get("section_ordinal"),
        page_number=stored.get("page_number"),
        heading=stored.get("heading"),
        heading_path=stored.get("heading_path") or [],
        start_char=stored.get("start_char"),
        end_char=stored.get("end_char"),
        excerpt=shown,
        excerpt_truncated=cut,
        source_truncated=stored.get("source_truncated"),
        source_available=chunk is not None,
        removed=bool(stored.get("removed")),
    )


def message_response(view: MessageView) -> MessageResponse:
    m = view.message
    assistant = m.role == "assistant"
    reason = m.meta.get("insufficient_reason") if assistant else None
    return MessageResponse(
        id=m.id,
        sequence=m.sequence,
        role=m.role,
        status=m.status,
        content=m.content,
        created_at=m.created_at,
        reply_to_id=m.reply_to_id,
        failure_reason=m.failure_reason,
        insufficient_evidence=(reason is not None) if assistant else None,
        insufficient_reason=reason,
        citations=[_citation(c, view) for c in m.citations],
        details=(
            {k: v for k, v in m.meta.items() if k != "insufficient_reason"} if assistant else None
        ),
    )
