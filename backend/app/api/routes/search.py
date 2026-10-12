from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.responses import envelope
from app.dependencies import (
    CurrentUser,
    HybridSearchServiceDep,
    SearchServiceDep,
    enforce_search_rate_limit,
)
from app.schemas.common import ErrorResponse, ResponseEnvelope
from app.schemas.search import (
    HybridHitResponse,
    HybridSearchInfo,
    HybridSearchRequest,
    HybridSearchResponse,
    SearchHitResponse,
    SearchInfo,
    SemanticSearchRequest,
    SemanticSearchResponse,
)

router = APIRouter(prefix="/search", tags=["search"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    404: {"model": ErrorResponse, "description": "A filtered document or collection is not yours"},
    422: {"model": ErrorResponse, "description": "Invalid query or parameters"},
    429: {"model": ErrorResponse, "description": "Too many requests"},
    503: {"model": ErrorResponse, "description": "Semantic search is temporarily unavailable"},
}


@router.post(
    "/semantic",
    summary="Semantic search over your documents",
    description=(
        "Embeds the query and returns the closest chunks of your own searchable documents by "
        "cosine similarity. `score` is a ranking signal for this query and model, not a "
        "probability. The query is sent in the body so it never appears in URLs or access logs."
    ),
    responses=_ERRORS,
    dependencies=[Depends(enforce_search_rate_limit)],
)
async def semantic_search(
    body: SemanticSearchRequest,
    request: Request,
    user: CurrentUser,
    service: SearchServiceDep,
) -> ResponseEnvelope[SemanticSearchResponse]:
    outcome = await service.semantic(
        user,
        query=body.query,
        top_k=body.top_k,
        document_ids=body.document_ids,
        collection_id=body.collection_id,
        min_score=body.min_score,
    )
    results = [
        SearchHitResponse(
            rank=item.rank,
            chunk_id=item.hit.chunk_id,
            document_id=item.hit.document_id,
            document_name=item.hit.document_name,
            chunk_index=item.hit.chunk_index,
            section_ordinal=item.hit.section_ordinal,
            page_number=item.hit.page_number,
            heading=item.hit.heading,
            heading_level=item.hit.heading_level,
            heading_path=list(item.hit.heading_path),
            text=item.hit.text,
            start_char=item.hit.start_char,
            end_char=item.hit.end_char,
            score=round(item.score, 6),
            distance=round(item.hit.distance, 6),
        )
        for item in outcome.results
    ]
    info = SearchInfo(
        embedding_model=outcome.config.model,
        model_revision=outcome.config.revision,
        metric="cosine",
        score_type="cosine_similarity",
        top_k=outcome.top_k,
        returned=len(results),
        latency_ms=outcome.latency_ms,
    )
    return envelope(request, SemanticSearchResponse(results=results, search=info))


@router.post(
    "/hybrid",
    summary="Hybrid (semantic plus keyword) search over your documents",
    description=(
        "Runs semantic (vector) and lexical (PostgreSQL full-text) retrieval over your own "
        "searchable documents and fuses the two rankings with Reciprocal Rank Fusion. `score` "
        "orders results for this query; it is not a probability or a cosine similarity. "
        "`dense_rank` and `sparse_rank` show where each retriever placed a result. If one "
        "retriever is unavailable the other still answers and `degraded` says so. The query is "
        "sent in the body so it never appears in URLs or access logs."
    ),
    responses=_ERRORS,
    dependencies=[Depends(enforce_search_rate_limit)],
)
async def hybrid_search(
    body: HybridSearchRequest,
    request: Request,
    user: CurrentUser,
    service: HybridSearchServiceDep,
) -> ResponseEnvelope[HybridSearchResponse]:
    outcome = await service.search(
        user,
        query=body.query,
        top_k=body.top_k,
        document_ids=body.document_ids,
        collection_id=body.collection_id,
    )
    results = [
        HybridHitResponse(
            rank=item.rank,
            chunk_id=item.record.chunk_id,
            document_id=item.record.document_id,
            document_name=item.record.document_name,
            chunk_index=item.record.chunk_index,
            section_ordinal=item.record.section_ordinal,
            page_number=item.record.page_number,
            heading=item.record.heading,
            heading_level=item.record.heading_level,
            heading_path=list(item.record.heading_path),
            text=item.record.text,
            start_char=item.record.start_char,
            end_char=item.record.end_char,
            score=round(item.score, 8),
            dense_rank=item.dense_rank,
            sparse_rank=item.sparse_rank,
            dense_score=None if item.dense_score is None else round(item.dense_score, 6),
        )
        for item in outcome.results
    ]
    diagnostics = outcome.diagnostics
    info = HybridSearchInfo(
        rrf_k=outcome.rrf_k,
        top_k=outcome.top_k,
        returned=len(results),
        dense_candidates=diagnostics.dense_candidates,
        sparse_candidates=diagnostics.sparse_candidates,
        overlap=diagnostics.overlap,
        retrievers_used=list(diagnostics.retrievers_used),
        degraded=list(diagnostics.degraded),
        embedding_model=outcome.config.model,
        model_revision=outcome.config.revision,
        latency_ms=outcome.latency_ms,
    )
    return envelope(request, HybridSearchResponse(results=results, search=info))
