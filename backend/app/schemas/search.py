import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

QueryText = Annotated[str, Field(min_length=1, max_length=10_000)]


class SemanticSearchRequest(BaseModel):
    """Semantic search over the caller's own searchable documents."""

    query: QueryText = Field(
        description="Natural-language query (any language). Sent in the body, never in a URL."
    )
    top_k: int | None = Field(
        default=None, ge=1, le=200, description="Number of chunks to return (server limit applies)"
    )
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=100,
        description="Restrict to these documents. All must be yours, otherwise 404.",
    )
    collection_id: uuid.UUID | None = Field(
        default=None, description="Restrict to the documents of one of your collections"
    )
    min_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Drop results with a cosine similarity below this value",
    )


class SearchHitResponse(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    chunk_index: int
    section_ordinal: int
    page_number: int | None
    heading: str | None
    heading_level: int | None
    heading_path: list[str]
    text: str
    start_char: int
    end_char: int
    score: float = Field(
        description="Cosine similarity between query and chunk, in [-1, 1] (1 - distance). "
        "A relative ranking signal for one query and model; not a probability or a relevance "
        "percentage, and not comparable across models."
    )
    distance: float = Field(description="Cosine distance, 1 - score, in [0, 2]")


class SearchInfo(BaseModel):
    embedding_model: str
    model_revision: str
    metric: Literal["cosine"]
    score_type: Literal["cosine_similarity"]
    top_k: int
    returned: int
    searched_documents: Literal["own, ready"] = "own, ready"
    latency_ms: int


class SemanticSearchResponse(BaseModel):
    results: list[SearchHitResponse]
    search: SearchInfo


class HybridSearchRequest(BaseModel):
    """Hybrid (dense plus lexical) search over the caller's own searchable documents."""

    query: QueryText = Field(
        description="Natural-language or keyword query in any language. Sent in the body."
    )
    top_k: int | None = Field(
        default=None, ge=1, le=200, description="Number of chunks to return (server limit applies)"
    )
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=100,
        description="Restrict to these documents. All must be yours, otherwise 404.",
    )
    collection_id: uuid.UUID | None = Field(
        default=None, description="Restrict to the documents of one of your collections"
    )
    rerank: bool = Field(
        default=False,
        description="Re-score the best candidates with a cross-encoder. Off by default; the "
        "response then has the same shape and order as before.",
    )
    rerank_candidates: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="How many hybrid candidates to rerank (server limit applies; `top_k` must "
        "not exceed it). Only used with `rerank`.",
    )


class HybridHitResponse(BaseModel):
    rank: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    chunk_index: int
    section_ordinal: int
    page_number: int | None
    heading: str | None
    heading_level: int | None
    heading_path: list[str]
    text: str
    start_char: int
    end_char: int
    score: float = Field(
        description="Reciprocal Rank Fusion score: the sum over retrievers of weight / (k + rank). "
        "It orders results for this query; it is not a probability, not a cosine similarity and "
        "not comparable across queries or configurations."
    )
    dense_rank: int | None = Field(
        description="1-based rank in the semantic candidate list, null if it was not a candidate"
    )
    sparse_rank: int | None = Field(
        description="1-based rank in the lexical candidate list, null if it was not a candidate"
    )
    dense_score: float | None = Field(
        description="Cosine similarity to the query (informational), null if not a dense candidate"
    )


class HybridSearchInfo(BaseModel):
    mode: Literal["hybrid"] = "hybrid"
    fusion: Literal["rrf"] = "rrf"
    rrf_k: int
    top_k: int
    returned: int
    dense_candidates: int
    sparse_candidates: int
    overlap: int = Field(description="Chunks that were candidates of both retrievers")
    retrievers_used: list[str]
    degraded: list[str] = Field(
        description="Retrievers that failed; the others still answered (empty when all worked)"
    )
    embedding_model: str
    model_revision: str
    searched_documents: Literal["own, ready"] = "own, ready"
    latency_ms: int


class HybridSearchResponse(BaseModel):
    results: list[HybridHitResponse]
    search: HybridSearchInfo


class RerankedHitResponse(HybridHitResponse):
    """A hybrid result after reranking. `rank` is the final position; `score`, `dense_rank`,
    `sparse_rank` and `dense_score` are unchanged from retrieval."""

    retrieval_rank: int = Field(description="Position in the hybrid ranking before reranking")
    rerank_score: float | None = Field(
        description="The cross-encoder's raw output (a logit): higher is more relevant to this "
        "query. Not a probability, not comparable across queries or models, never stored. "
        "Null when reranking did not happen."
    )
    rerank_truncated: bool | None = Field(
        description="True if the passage was longer than the model's input limit and its end "
        "was not scored; null when reranking did not happen"
    )


class RerankInfo(BaseModel):
    requested: Literal[True] = True
    applied: bool = Field(
        description="False if reranking failed and the hybrid order was kept (see "
        "`fallback_reason`); `degraded` then contains `rerank`"
    )
    fallback_reason: Literal["unavailable", "busy", "timeout", "failed", "invalid"] | None
    score_type: Literal["raw_logit"] = "raw_logit"
    model: str
    model_revision: str
    device: str
    candidates: int = Field(description="Candidates offered to the reranker")
    truncated_candidates: int = Field(description="Candidates longer than the model input limit")
    latency_ms: int = Field(description="Time spent reranking, including a model load if needed")


class RerankedSearchInfo(HybridSearchInfo):
    rerank: RerankInfo


class RerankedSearchResponse(BaseModel):
    results: list[RerankedHitResponse]
    search: RerankedSearchInfo
