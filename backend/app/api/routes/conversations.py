import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api.pagination import DEFAULT_PAGE_SIZE, PageParam, PageSizeParam
from app.api.responses import envelope, paged
from app.dependencies import (
    ConversationServiceDep,
    CurrentUser,
    enforce_conversation_manage_rate_limit,
    enforce_conversation_message_rate_limit,
)
from app.exceptions import RequestValidationFailedError
from app.schemas.common import ErrorResponse, PagedResponse, ResponseEnvelope
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationResponse,
    ConversationUpdate,
    MessagePage,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services.conversation_presenter import conversation_response, message_response

router = APIRouter(prefix="/conversations", tags=["conversations"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    404: {"model": ErrorResponse, "description": "Not found (or not yours)"},
    429: {"model": ErrorResponse, "description": "Too many requests"},
}
_MESSAGE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    409: {"model": ErrorResponse, "description": "Busy, in progress, key reused, or full"},
    422: {"model": ErrorResponse, "description": "Invalid question or parameters"},
    502: {"model": ErrorResponse, "description": "The generated output could not be validated"},
    503: {"model": ErrorResponse, "description": "Answer generation is disabled or unavailable"},
}
_KEY = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
_MANAGE = [Depends(enforce_conversation_manage_rate_limit)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
    responses=_ERRORS,
    dependencies=_MANAGE,
)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> ResponseEnvelope[ConversationResponse]:
    return envelope(request, conversation_response(await service.create(user, body.title)))


@router.get("", summary="List your conversations", responses=_ERRORS, dependencies=_MANAGE)
async def list_conversations(
    request: Request,
    user: CurrentUser,
    service: ConversationServiceDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
) -> PagedResponse[ConversationResponse]:
    views, total = await service.list_page(user, page=page, page_size=page_size)
    return paged(
        request,
        [conversation_response(v) for v in views],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{conversation_id}",
    summary="Get a conversation and a page of its messages",
    responses=_ERRORS,
    dependencies=_MANAGE,
)
async def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    service: ConversationServiceDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
    order: Annotated[Literal["asc", "desc"], Query(description="By message number")] = "asc",
) -> ResponseEnvelope[ConversationDetail]:
    view, messages, total = await service.get(
        user, conversation_id, page=page, page_size=page_size, descending=order == "desc"
    )
    return envelope(
        request,
        ConversationDetail(
            conversation=conversation_response(view),
            messages=[message_response(m) for m in messages],
            page=MessagePage(
                page=page,
                page_size=page_size,
                total_messages=total,
                total_pages=-(-total // page_size),
                order=order,
            ),
        ),
    )


@router.patch(
    "/{conversation_id}", summary="Rename a conversation", responses=_ERRORS, dependencies=_MANAGE
)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    request: Request,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> ResponseEnvelope[ConversationResponse]:
    view = await service.rename(user, conversation_id, body.title)
    return envelope(request, conversation_response(view))


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and all its messages",
    responses=_ERRORS,
    dependencies=_MANAGE,
)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, service: ConversationServiceDep
) -> Response:
    await service.delete(user, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    summary="Ask a question in a conversation",
    description=(
        "Stores the question, answers it with the same pipeline as `POST /rag/answer` "
        "(owner-scoped retrieval, optional reranking, validated citations), then stores the "
        "answer. Earlier turns are passed to the model only as untrusted context for "
        "interpreting a follow-up; facts and citations always come from the documents "
        "retrieved for this question. Document filters apply to this question only. A failed "
        "generation keeps the question (status `failed`) and stores no answer; retry with the "
        "same `Idempotency-Key` to answer that same question again, or to get the stored "
        "answer back (200) if it was already answered. Only one question per conversation is "
        "answered at a time (409 otherwise). There is no role field: clients cannot write "
        "assistant messages."
    ),
    responses=_MESSAGE_ERRORS,
    dependencies=[Depends(enforce_conversation_message_rate_limit)],
)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    request: Request,
    response: Response,
    user: CurrentUser,
    service: ConversationServiceDep,
    idempotency_key: Annotated[
        str | None, Header(max_length=200, description="Optional; 1-64 of A-Z a-z 0-9 _ . : -")
    ] = None,
) -> ResponseEnvelope[SendMessageResponse]:
    if idempotency_key is not None and not _KEY.fullmatch(idempotency_key):
        raise RequestValidationFailedError("Idempotency-Key must be 1-64 of A-Z a-z 0-9 _ . : -")
    result = await service.send_message(user, conversation_id, body, idempotency_key)
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return envelope(
        request,
        SendMessageResponse(
            conversation=conversation_response(result.conversation),
            user_message=message_response(result.question),
            assistant_message=message_response(result.answer),
            replayed=result.replayed,
        ),
    )
