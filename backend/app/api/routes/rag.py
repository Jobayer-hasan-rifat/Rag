from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.responses import envelope
from app.dependencies import CurrentUser, RagServiceDep, enforce_rag_rate_limit
from app.exceptions import RagDisabledError
from app.schemas.common import ErrorResponse, ResponseEnvelope
from app.schemas.rag import (
    RagAnswerRequest,
    RagAnswerResponse,
)
from app.services.rag_presenter import build_response

router = APIRouter(prefix="/rag", tags=["rag"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    404: {"model": ErrorResponse, "description": "A filtered document or collection is not yours"},
    422: {"model": ErrorResponse, "description": "Invalid question or parameters"},
    429: {"model": ErrorResponse, "description": "Too many requests"},
    502: {"model": ErrorResponse, "description": "The generated output could not be validated"},
    503: {"model": ErrorResponse, "description": "Answer generation is disabled or unavailable"},
}


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
    return envelope(request, build_response(outcome))
