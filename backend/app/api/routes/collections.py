import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.pagination import DEFAULT_PAGE_SIZE, PageParam, PageSizeParam, SearchParam
from app.api.responses import envelope, paged
from app.dependencies import CollectionServiceDep, CurrentUser
from app.schemas.collection import (
    AddDocumentsRequest,
    AddDocumentsResult,
    CollectionCreate,
    CollectionResponse,
    CollectionUpdate,
)
from app.schemas.common import ErrorResponse, PagedResponse, ResponseEnvelope
from app.services.collection_service import CollectionView

router = APIRouter(prefix="/collections", tags=["collections"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    404: {"model": ErrorResponse, "description": "Not found (or not yours)"},
}


def _to_response(view: CollectionView) -> CollectionResponse:
    collection = view.collection
    return CollectionResponse(
        id=collection.id,
        name=collection.name,
        description=collection.description,
        document_count=view.document_count,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


@router.get("", summary="List your collections", responses=_ERRORS)
async def list_collections(
    request: Request,
    user: CurrentUser,
    service: CollectionServiceDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
    search: SearchParam = None,
) -> PagedResponse[CollectionResponse]:
    views, total = await service.list_collections(
        user, search=search, page=page, page_size=page_size
    )
    return paged(
        request, [_to_response(v) for v in views], page=page, page_size=page_size, total=total
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a collection",
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "Name already in use"}},
)
async def create_collection(
    body: CollectionCreate, request: Request, user: CurrentUser, service: CollectionServiceDep
) -> ResponseEnvelope[CollectionResponse]:
    return envelope(request, _to_response(await service.create(user, body)))


@router.get("/{collection_id}", summary="Get a collection", responses=_ERRORS)
async def get_collection(
    collection_id: uuid.UUID, request: Request, user: CurrentUser, service: CollectionServiceDep
) -> ResponseEnvelope[CollectionResponse]:
    return envelope(request, _to_response(await service.get(user, collection_id)))


@router.patch("/{collection_id}", summary="Update a collection", responses=_ERRORS)
async def update_collection(
    collection_id: uuid.UUID,
    body: CollectionUpdate,
    request: Request,
    user: CurrentUser,
    service: CollectionServiceDep,
) -> ResponseEnvelope[CollectionResponse]:
    return envelope(request, _to_response(await service.update(user, collection_id, body)))


@router.delete(
    "/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a collection (its documents are kept)",
    responses=_ERRORS,
)
async def delete_collection(
    collection_id: uuid.UUID, user: CurrentUser, service: CollectionServiceDep
) -> Response:
    await service.delete(user, collection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{collection_id}/documents", summary="Add documents to a collection", responses=_ERRORS
)
async def add_documents(
    collection_id: uuid.UUID,
    body: AddDocumentsRequest,
    request: Request,
    user: CurrentUser,
    service: CollectionServiceDep,
) -> ResponseEnvelope[AddDocumentsResult]:
    return envelope(request, await service.add_documents(user, collection_id, body.document_ids))


@router.delete(
    "/{collection_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a document from a collection",
    responses=_ERRORS,
)
async def remove_document(
    collection_id: uuid.UUID,
    document_id: uuid.UUID,
    user: CurrentUser,
    service: CollectionServiceDep,
) -> Response:
    await service.remove_document(user, collection_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
