from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.responses import envelope
from app.dependencies import CurrentUser, RagServiceDep, enforce_rag_rate_limit
from app.exceptions import RagDisabledError
from app.schemas.common import ErrorResponse, ResponseEnvelope
from app.schemas.rag import (
    CitationResponse,
    RagAnswerRequest,
    RagAnswerResponse,
    RagContextInfo,
    RagGenerationInfo,
    RagRetrievalInfo,
    RagTimings,
    RagValidationInfo,
)
from app.services.rag_service import Citation, RagOutcome

router = APIRouter(prefix="/rag", tags=["rag"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    404: {"model": ErrorResponse, "description": "A filtered document or collection is not yours"},
    422: {"model": ErrorResponse, "description": "Invalid question or parameters"},
    429: {"model": ErrorResponse, "description": "Too many requests"},
    502: {"model": ErrorResponse, "description": "The generated output could not be validated"},
    503: {"model": ErrorResponse, "description": "Answer generation is disabled or unavailable"},
}


def _citation(item: Citation) -> CitationResponse:
    record = item.block.record
    return CitationResponse(
        label=item.block.label,
        document_id=record.document_id,
        chunk_id=record.chunk_id,
        document_name=record.document_name,
        chunk_index=record.chunk_index,
        section_ordinal=record.section_ordinal,
        page_number=record.page_number,
        heading=record.heading,
        heading_path=list(record.heading_path),
        start_char=record.start_char,
        end_char=record.end_char,
        excerpt=item.excerpt,
        excerpt_truncated=item.excerpt_truncated,
        source_truncated=item.block.truncated,
    )


def _response(outcome: RagOutcome) -> RagAnswerResponse:
    retrieval = outcome.retrieval
    diagnostics = retrieval.diagnostics
    grounded = outcome.grounded
    generation = None
    if outcome.generation is not None:
        result = outcome.generation.result
        generation = RagGenerationInfo(
            provider=outcome.generation.provider,
            model=outcome.generation.model,
            prompt_version=outcome.generation.prompt_version,
            attempts=outcome.generation.attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            finish_reason=result.finish_reason,
            latency_ms=result.latency_ms,
        )
    return RagAnswerResponse(
        answer=outcome.answer,
        insufficient_evidence=outcome.insufficient,
        insufficient_reason=outcome.insufficient_reason,
        citations=[_citation(item) for item in outcome.citations],
        retrieval=RagRetrievalInfo(
            mode=outcome.mode,
            candidates=len(retrieval.results),
            dense_candidates=diagnostics.dense_candidates,
            sparse_candidates=diagnostics.sparse_candidates,
            retrievers_used=list(diagnostics.retrievers_used),
            degraded=outcome.degraded,
            reranked=outcome.reranked,
            embedding_model=retrieval.config.model,
            model_revision=retrieval.config.revision,
        ),
        context=RagContextInfo(
            sources_supplied=len(outcome.context.blocks),
            duplicates_dropped=outcome.context.duplicates_dropped,
            truncated_sources=outcome.context.truncated_blocks,
            estimated_input_tokens=outcome.context.estimated_tokens,
            budget_tokens=outcome.context.budget_tokens,
        ),
        generation=generation,
        validation=(
            None
            if grounded is None
            else RagValidationInfo(
                unknown_labels_removed=grounded.removed_unknown_labels,
                uncited_sentences_removed=grounded.removed_uncited_sentences,
                fabricated_quotes_removed=grounded.removed_fabricated_quotes,
                unsupported_literals_removed=grounded.removed_unsupported_literals,
                citation_granularity=grounded.granularity,
            )
        ),
        timings=RagTimings(**outcome.timings),
    )


@router.post(
    "/answer",
    summary="Answer a question from your documents, with validated citations",
    description=(
        "Retrieves from your own searchable documents (hybrid or semantic, optionally reranked), "
        "shows a bounded set of sources to the configured language model and returns an answer "
        "whose every sentence cites sources by label (`S1`, `S2`, ...). Citations are built from "
        "database records of the sources actually shown to the model; labels the model invented "
        "are removed, and sentences with an unsupported quotation, number or identifier are "
        "dropped. If the documents do not contain enough evidence, `insufficient_evidence` is "
        "true and the model may not have been called at all. Retrieved text is treated as "
        "untrusted data, but no prompt can fully prevent a model from being misled by it. "
        "Nothing is stored. Provider failures return 503 or 502, never an invented answer."
    ),
    responses=_ERRORS,
    dependencies=[Depends(enforce_rag_rate_limit)],
)
async def answer(
    body: RagAnswerRequest,
    request: Request,
    user: CurrentUser,
    service: RagServiceDep,
) -> ResponseEnvelope[RagAnswerResponse]:
    if service is None:
        raise RagDisabledError()
    outcome = await service.answer(
        user,
        question=body.question,
        document_ids=body.document_ids,
        collection_id=body.collection_id,
        retrieval=body.retrieval,
        rerank=body.rerank,
        max_sources=body.max_sources,
        max_answer_tokens=body.max_answer_tokens,
    )
    return envelope(request, _response(outcome))
