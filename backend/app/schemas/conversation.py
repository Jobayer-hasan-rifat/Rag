import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search import QueryText


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional title (at most 200 characters after normalisation). If omitted the "
        "conversation is titled from its first question.",
    )


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=1000)


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class MessageCitation(BaseModel):
    """A source an answer cited. Identifiers and metadata were stored with the answer; the
    excerpt is read from the chunk when the message is shown, so it is absent once the chunk is
    gone. `removed` means the document was deleted and only identifiers remain."""

    label: str
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    document_name: str | None = None
    chunk_index: int | None = None
    section_ordinal: int | None = None
    page_number: int | None = None
    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    start_char: int | None = None
    end_char: int | None = None
    excerpt: str | None = None
    excerpt_truncated: bool | None = None
    source_truncated: bool | None = None
    source_available: bool = Field(
        description="False if the chunk no longer exists, was re-chunked, or is not searchable"
    )
    removed: bool = False


class MessageResponse(BaseModel):
    id: uuid.UUID
    sequence: int
    role: Literal["user", "assistant"]
    status: Literal["processing", "answered", "failed", "complete"]
    content: str
    created_at: datetime
    reply_to_id: uuid.UUID | None = None
    failure_reason: str | None = Field(
        default=None, description="Why a question was not answered (interrupted, timeout, ...)"
    )
    insufficient_evidence: bool | None = Field(
        default=None, description="Assistant messages: the documents did not answer the question"
    )
    insufficient_reason: str | None = None
    citations: list[MessageCitation] = Field(default_factory=list)
    details: dict[str, Any] | None = Field(
        default=None,
        description="Assistant messages: retrieval, context, generation, validation and timings",
    )


class MessagePage(BaseModel):
    page: int
    page_size: int
    total_messages: int
    total_pages: int
    order: Literal["asc", "desc"]


class ConversationDetail(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    page: MessagePage


class SendMessageRequest(BaseModel):
    """A question for the conversation. There is no role or owner field: the server decides both,
    and unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    content: QueryText = Field(description="The question. Sent in the body, never in a URL.")
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=100,
        description="Restrict this question to these documents (yours, otherwise 404). Filters "
        "are not remembered: send them with every question.",
    )
    collection_id: uuid.UUID | None = None
    retrieval: Literal["hybrid", "semantic"] | None = None
    rerank: bool = False
    max_sources: int | None = Field(default=None, ge=1, le=20)
    max_answer_tokens: int | None = Field(default=None, ge=16, le=4096)


class SendMessageResponse(BaseModel):
    conversation: ConversationResponse
    user_message: MessageResponse
    assistant_message: MessageResponse
    replayed: bool = Field(
        description="True if this idempotency key had already been answered and the stored result "
        "is returned without generating again"
    )
