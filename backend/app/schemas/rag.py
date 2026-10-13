import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.search import QueryText


class RagAnswerRequest(BaseModel):
    """Ask a question; the answer is generated from your own searchable documents only."""

    question: QueryText = Field(
        description="The question, in Bangla, English or both. Sent in the body, never in a URL."
    )
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=100,
        description="Restrict to these documents. All must be yours, otherwise 404.",
    )
    collection_id: uuid.UUID | None = Field(
        default=None, description="Restrict to the documents of one of your collections"
    )
    retrieval: Literal["hybrid", "semantic"] | None = Field(
        default=None, description="Retrieval mode; the server default applies when omitted"
    )
    rerank: bool = Field(
        default=False,
        description="Re-score the candidates with the cross-encoder first (only if the server "
        "has reranking enabled). If it cannot run, the retrieval order is used and reported.",
    )
    max_sources: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Most sources shown to the model (server limit applies)",
    )
    max_answer_tokens: int | None = Field(
        default=None, ge=16, le=4096, description="Output token limit (server limit applies)"
    )


class CitationResponse(BaseModel):
    """A source the answer cites. Everything here comes from the database record of a chunk that
    was shown to the model in this request, never from the model's own text."""

    label: str = Field(description="The marker used in the answer, for example S1")
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    document_name: str
    chunk_index: int
    section_ordinal: int
    page_number: int | None = Field(description="Null for formats without reliable page numbers")
    heading: str | None
    heading_path: list[str]
    start_char: int
    end_char: int
    excerpt: str = Field(description="The beginning of the text the model was shown (bounded)")
    excerpt_truncated: bool
    source_truncated: bool = Field(
        description="True if the chunk was longer than the per-source limit; its end was not shown"
    )


class RagRetrievalInfo(BaseModel):
    mode: Literal["hybrid", "semantic"]
    candidates: int = Field(description="Chunks retrieved before selection")
    dense_candidates: int
    sparse_candidates: int
    retrievers_used: list[str]
    degraded: list[str] = Field(
        description="Retrieval or reranking steps that failed; the answer used what remained"
    )
    reranked: bool
    embedding_model: str
    model_revision: str


class RagContextInfo(BaseModel):
    sources_supplied: int
    duplicates_dropped: int
    truncated_sources: int
    estimated_input_tokens: int = Field(description="Conservative estimate of the context part")
    budget_tokens: int


class RagGenerationInfo(BaseModel):
    provider: str
    model: str
    prompt_version: str
    attempts: int
    input_tokens: int | None = Field(description="As reported by the provider, if it reports it")
    output_tokens: int | None
    finish_reason: str | None
    latency_ms: int


class RagValidationInfo(BaseModel):
    unknown_labels_removed: int
    uncited_sentences_removed: int
    fabricated_quotes_removed: int
    unsupported_literals_removed: int
    citation_granularity: Literal["sentence", "answer"] = Field(
        description="sentence: every kept sentence carries its own [S1] markers. answer: the model "
        "listed its sources but wrote no inline markers, so the listed sources apply to the whole "
        "answer"
    )


class RagTimings(BaseModel):
    retrieval_ms: int
    rerank_ms: int
    context_ms: int
    generation_ms: int
    total_ms: int


class RagAnswerResponse(BaseModel):
    answer: str = Field(
        description="The validated answer with [S1]-style markers, or a fixed message when the "
        "documents do not contain enough evidence"
    )
    insufficient_evidence: bool
    insufficient_reason: (
        Literal["no_sources", "retrieval_degraded", "model_abstained", "no_supported_sentences"]
        | None
    ) = Field(
        description="Why there is no answer: nothing was retrieved (the model was not called), "
        "nothing was retrieved and part of the search was unavailable, the model said the "
        "sources do not answer it, or every sentence failed validation"
    )
    citations: list[CitationResponse]
    retrieval: RagRetrievalInfo
    context: RagContextInfo
    generation: RagGenerationInfo | None = Field(description="Null when the model was not called")
    validation: RagValidationInfo | None
    timings: RagTimings
