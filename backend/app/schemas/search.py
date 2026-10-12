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
