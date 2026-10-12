from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.responses import envelope
from app.config import Settings
from app.dependencies import (
    CurrentUser,
    HybridSearchServiceDep,
    RerankServiceDep,
    SearchServiceDep,
    SettingsDep,
    enforce_search_rate_limit,
)
from app.exceptions import RequestValidationFailedError, RerankingDisabledError
from app.models.user import User
from app.reranking.ranking import RerankInput
from app.schemas.common import ErrorResponse, ResponseEnvelope
from app.schemas.search import (
    HybridHitResponse,
    HybridSearchInfo,
    HybridSearchRequest,
    HybridSearchResponse,
    RerankedHitResponse,
    RerankedSearchInfo,
    RerankedSearchResponse,
    RerankInfo,
    SearchHitResponse,
    SearchInfo,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from app.services.hybrid_search_service import HybridOutcome, HybridResult, HybridSearchService
from app.services.rerank_service import RerankService
from app.services.search_common import prepare_query

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
        "retriever is unavailable the other still answers and `degraded` says so. With "
        "`rerank: true` the best candidates are re-scored by a cross-encoder (opt-in; "
        "`rerank_score` is a raw model output, not a probability); if reranking cannot run the "
        "hybrid order is kept and `rerank.applied` is false. The query is sent in the body so "
        "it never appears in URLs or access logs."
    ),
    responses=_ERRORS,
    dependencies=[Depends(enforce_search_rate_limit)],
)
async def hybrid_search(
    body: HybridSearchRequest,
    request: Request,
    user: CurrentUser,
    service: HybridSearchServiceDep,
    rerank: RerankServiceDep,
    settings: SettingsDep,
) -> ResponseEnvelope[RerankedSearchResponse | HybridSearchResponse]:
    if body.rerank:
        if rerank is None:
            raise RerankingDisabledError()
        return await _reranked_search(body, request, user, service, rerank, settings)
    outcome = await service.search(
        user,
        query=body.query,
        top_k=body.top_k,
        document_ids=body.document_ids,
        collection_id=body.collection_id,
    )
    results = [_hybrid_hit(item) for item in outcome.results]
    return envelope(
        request, HybridSearchResponse(results=results, search=_hybrid_info(outcome, results))
    )


def _hybrid_hit(item: HybridResult) -> HybridHitResponse:
    return HybridHitResponse(**_hit_fields(item))


def _hit_fields(item: HybridResult) -> dict[str, Any]:
    record = item.record
    return {
        "rank": item.rank,
        "chunk_id": record.chunk_id,
        "document_id": record.document_id,
        "document_name": record.document_name,
        "chunk_index": record.chunk_index,
        "section_ordinal": record.section_ordinal,
        "page_number": record.page_number,
        "heading": record.heading,
        "heading_level": record.heading_level,
        "heading_path": list(record.heading_path),
        "text": record.text,
        "start_char": record.start_char,
        "end_char": record.end_char,
        "score": round(item.score, 8),
        "dense_rank": item.dense_rank,
        "sparse_rank": item.sparse_rank,
        "dense_score": None if item.dense_score is None else round(item.dense_score, 6),
    }


def _hybrid_info(outcome: HybridOutcome, results: Sequence[object], **extra: Any) -> Any:
    diagnostics = outcome.diagnostics
    fields: dict[str, Any] = {
        "rrf_k": outcome.rrf_k,
        "top_k": outcome.top_k,
        "returned": len(results),
        "dense_candidates": diagnostics.dense_candidates,
        "sparse_candidates": diagnostics.sparse_candidates,
        "overlap": diagnostics.overlap,
        "retrievers_used": list(diagnostics.retrievers_used),
        "degraded": list(diagnostics.degraded),
        "embedding_model": outcome.config.model,
        "model_revision": outcome.config.revision,
        "latency_ms": outcome.latency_ms,
    }
    fields.update(extra)
    return (RerankedSearchInfo if "rerank" in extra else HybridSearchInfo)(**fields)


async def _reranked_search(
    body: HybridSearchRequest,
    request: Request,
    user: User,
    service: HybridSearchService,
    rerank: RerankService,
    settings: Settings,
) -> ResponseEnvelope[RerankedSearchResponse | HybridSearchResponse]:
    candidates = body.rerank_candidates or settings.rerank_candidates
    if candidates > settings.rerank_max_candidates:
        raise RequestValidationFailedError(
            f"rerank_candidates must be at most {settings.rerank_max_candidates}"
        )
    top_k = body.top_k or min(settings.search_default_top_k, candidates)
    if top_k > settings.search_max_top_k:
        raise RequestValidationFailedError(f"top_k must be at most {settings.search_max_top_k}")
    if top_k > candidates:
        raise RequestValidationFailedError("top_k must not exceed the number of candidates")
    # Only what retrieval returns for this user (owner-scoped, ready documents) is reranked.
    outcome = await service.search(
        user,
        query=body.query,
        top_k=None,
        document_ids=body.document_ids,
        collection_id=body.collection_id,
        candidate_limit=candidates,
    )
    await service.release_connection()  # no database connection is held during inference
    by_chunk = {item.record.chunk_id: item for item in outcome.results}
    reranked = await rerank.rerank(
        prepare_query(body.query, settings),
        [RerankInput(i.record.chunk_id, i.record.text, i.rank) for i in outcome.results],
        top_k=top_k,
    )
    model, revision, device = rerank.describe()
    rerank_info = RerankInfo(
        applied=reranked.applied,
        fallback_reason=reranked.fallback_reason,
        model=model,
        model_revision=revision,
        device=device,
        candidates=reranked.candidates,
        truncated_candidates=reranked.truncated,
        latency_ms=reranked.latency_ms,
    )
    results: list[RerankedHitResponse]
    if reranked.applied:
        results = [
            RerankedHitResponse(
                **{**_hit_fields(by_chunk[r.key]), "rank": r.rank},
                retrieval_rank=r.retrieval_rank,
                rerank_score=round(r.score, 6),
                rerank_truncated=r.truncated,
            )
            for r in reranked.items
        ]
    else:  # keep the hybrid order and say so: never a reranked-looking list
        results = [
            RerankedHitResponse(
                **_hit_fields(item),
                retrieval_rank=item.rank,
                rerank_score=None,
                rerank_truncated=None,
            )
            for item in outcome.results[:top_k]
        ]
    info = _hybrid_info(outcome, results, rerank=rerank_info)
    info.top_k = top_k
    if not reranked.applied:
        info.degraded = [*info.degraded, "rerank"]
    return envelope(request, RerankedSearchResponse(results=results, search=info))
