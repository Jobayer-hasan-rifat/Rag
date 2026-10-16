import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile

from app.api.http_headers import DOWNLOAD_SECURITY_HEADERS, attachment_disposition
from app.api.pagination import DEFAULT_PAGE_SIZE, PageParam, PageSizeParam, SearchParam
from app.api.responses import envelope, paged
from app.core.documents.lifecycle import DocumentStatus
from app.core.documents.types import DocumentType
from app.db.repositories.document_repository import DocumentFilters, SortField, SortOrder
from app.dependencies import (
    CurrentUser,
    DocumentServiceDep,
    enforce_retry_rate_limit,
    enforce_upload_rate_limit,
)
from app.exceptions import RequestValidationFailedError
from app.models.document import Document
from app.schemas.common import ErrorResponse, PagedResponse, ResponseEnvelope
from app.schemas.document import (
    ChunkResponse,
    CollectionSummary,
    DocumentResponse,
    DocumentUpdate,
    VersionResponse,
    VersionSummary,
    VersionUploadResponse,
)
from app.services.document_service import DocumentView

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_COLLECTIONS_PER_UPLOAD = 20

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    404: {"model": ErrorResponse, "description": "Not found (or not yours)"},
}
_UPLOAD_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "collection_ids": {
                            "type": "string",
                            "description": "Optional JSON array of your collection UUIDs",
                        },
                    },
                }
            }
        },
    }
}


def _to_response(view: DocumentView) -> DocumentResponse:
    document = view.document
    latest = view.latest
    return DocumentResponse(
        id=document.family_id,
        version_id=document.id,
        version_number=document.version_number,
        version_count=view.version_count,
        active_version_id=document.id if document.is_active else None,
        latest_version=(
            None
            if latest is None
            else VersionSummary(
                id=latest.id,
                version_number=latest.version_number,
                status=latest.status,
                failure_reason=latest.failure_reason,
            )
        ),
        filename=document.filename,
        file_type=document.file_type,
        content_type=document.content_type,
        file_size=document.file_size,
        checksum_sha256=document.checksum_sha256,
        status=document.status,
        error_message=document.error_message,
        failure_reason=document.failure_reason,
        processing_started_at=document.processing_started_at,
        processing_completed_at=document.processing_completed_at,
        page_count=document.page_count,
        character_count=document.character_count,
        chunk_count=document.chunk_count,
        chunking_version=document.chunking_version,
        embedded_chunk_count=document.embedded_chunk_count,
        embedding_completed_at=document.embedding_completed_at,
        searchable=document.status == DocumentStatus.READY and document.is_active,
        collections=[CollectionSummary(id=ref.id, name=ref.name) for ref in view.collections],
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _to_version(version: Document) -> VersionResponse:
    return VersionResponse(
        id=version.id,
        document_id=version.family_id,
        version_number=version.version_number,
        filename=version.filename,
        file_type=version.file_type,
        content_type=version.content_type,
        file_size=version.file_size,
        checksum_sha256=version.checksum_sha256,
        status=version.status,
        error_message=version.error_message,
        failure_reason=version.failure_reason,
        is_active=version.is_active,
        is_current=version.is_current,
        searchable=version.status == DocumentStatus.READY and version.is_active,
        chunk_count=version.chunk_count,
        embedded_chunk_count=version.embedded_chunk_count,
        processing_started_at=version.processing_started_at,
        processing_completed_at=version.processing_completed_at,
        embedding_completed_at=version.embedding_completed_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def _parse_collection_ids(raw: object) -> list[uuid.UUID]:
    if raw is None or raw == "":
        return []
    try:
        values = json.loads(str(raw))
        if not isinstance(values, list):
            raise ValueError("expected a JSON array")
        ids = [uuid.UUID(str(value)) for value in values]
    except (ValueError, TypeError):
        raise RequestValidationFailedError("collection_ids must be a JSON array of UUIDs") from None
    if len(ids) > MAX_COLLECTIONS_PER_UPLOAD:
        raise RequestValidationFailedError(
            f"At most {MAX_COLLECTIONS_PER_UPLOAD} collections per upload"
        )
    return ids


@router.get("", summary="List your documents", responses=_ERRORS)
async def list_documents(
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    file_type: DocumentType | None = None,
    collection_id: uuid.UUID | None = None,
    search: SearchParam = None,
    sort: SortField = "created_at",
    order: SortOrder = "desc",
) -> PagedResponse[DocumentResponse]:
    filters = DocumentFilters(
        status=status_filter.value if status_filter else None,
        file_type=file_type.value if file_type else None,
        collection_id=collection_id,
        search=search,
    )
    views, total = await service.list_documents(
        user, filters=filters, sort=sort, order=order, page=page, page_size=page_size
    )
    return paged(
        request, [_to_response(v) for v in views], page=page, page_size=page_size, total=total
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document (multipart/form-data)",
    openapi_extra=_UPLOAD_OPENAPI,
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "Identical document already uploaded"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported or mismatched file type"},
        429: {"model": ErrorResponse, "description": "Too many uploads"},
    },
    dependencies=[Depends(enforce_upload_rate_limit)],
)
async def upload_document(
    request: Request, user: CurrentUser, service: DocumentServiceDep
) -> ResponseEnvelope[DocumentResponse]:
    # The multipart body is parsed here, after authentication and rate limiting have passed.
    form = await request.form(max_files=1, max_fields=1)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise RequestValidationFailedError("A 'file' part is required")
        view = await service.upload(
            actor=user,
            filename=upload.filename,
            content_type=upload.content_type,
            file=upload.file,
            collection_ids=_parse_collection_ids(form.get("collection_ids")),
        )
    finally:
        await form.close()
    return envelope(request, _to_response(view))


@router.get("/{document_id}", summary="Get document metadata", responses=_ERRORS)
async def get_document(
    document_id: uuid.UUID, request: Request, user: CurrentUser, service: DocumentServiceDep
) -> ResponseEnvelope[DocumentResponse]:
    return envelope(request, _to_response(await service.get(user, document_id)))


@router.patch("/{document_id}", summary="Rename a document", responses=_ERRORS)
async def update_document(
    document_id: uuid.UUID,
    body: DocumentUpdate,
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
) -> ResponseEnvelope[DocumentResponse]:
    return envelope(request, _to_response(await service.rename(user, document_id, body.filename)))


@router.get(
    "/{document_id}/chunks",
    summary="Inspect a document's chunks (reading order, with source locations)",
    responses=_ERRORS,
)
async def list_document_chunks(
    document_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
) -> PagedResponse[ChunkResponse]:
    rows, total = await service.list_chunks(user, document_id, page=page, page_size=page_size)
    items = [
        ChunkResponse(
            id=chunk.id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            char_count=chunk.char_count,
            section_ordinal=section_ordinal,
            page_number=chunk.page_number,
            heading=chunk.heading,
            heading_level=chunk.heading_level,
            heading_path=list(chunk.heading_path),
            start_char=chunk.start_char,
            end_char=chunk.end_char,
            overlap_chars=chunk.overlap_chars,
        )
        for chunk, section_ordinal in rows
    ]
    return paged(request, items, page=page, page_size=page_size, total=total)


@router.post(
    "/{document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-queue processing of a failed document",
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "Only failed documents can be retried"},
        429: {"model": ErrorResponse, "description": "Too many requests"},
    },
    dependencies=[Depends(enforce_retry_rate_limit)],
)
async def retry_document(
    document_id: uuid.UUID, request: Request, user: CurrentUser, service: DocumentServiceDep
) -> ResponseEnvelope[DocumentResponse]:
    return envelope(request, _to_response(await service.retry_processing(user, document_id)))


@router.get(
    "/{document_id}/download",
    summary="Download the original file",
    response_class=StreamingResponse,
    responses=_ERRORS,
)
async def download_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> StreamingResponse:
    result = await service.download(user, document_id)
    document = result.document
    return StreamingResponse(
        result.chunks,
        media_type=document.content_type,
        headers={
            "Content-Disposition": attachment_disposition(document.filename),
            "Content-Length": str(document.file_size),
            **DOWNLOAD_SECURITY_HEADERS,
        },
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its stored file",
    responses=_ERRORS,
)
async def delete_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> Response:
    await service.delete(user, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- versions ----------------------------------------------------------------------------------


@router.get(
    "/{document_id}/versions",
    summary="List a document's versions (newest first)",
    responses=_ERRORS,
)
async def list_versions(
    document_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
    page: PageParam = 1,
    page_size: PageSizeParam = DEFAULT_PAGE_SIZE,
) -> PagedResponse[VersionResponse]:
    versions, total = await service.list_versions(user, document_id, page=page, page_size=page_size)
    return paged(
        request, [_to_version(v) for v in versions], page=page, page_size=page_size, total=total
    )


@router.post(
    "/{document_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a replacement as a new version (multipart/form-data)",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
    responses={
        **_ERRORS,
        200: {"model": ResponseEnvelope[VersionUploadResponse], "description": "Unchanged"},
        409: {"model": ErrorResponse, "description": "Duplicate of another document, or limit"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported or mismatched file type"},
        429: {"model": ErrorResponse, "description": "Too many uploads"},
    },
    dependencies=[Depends(enforce_upload_rate_limit)],
)
async def upload_version(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    user: CurrentUser,
    service: DocumentServiceDep,
) -> ResponseEnvelope[VersionUploadResponse]:
    form = await request.form(max_files=1, max_fields=1)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise RequestValidationFailedError("A 'file' part is required")
        result = await service.upload_version(
            actor=user,
            document_id=document_id,
            filename=upload.filename,
            content_type=upload.content_type,
            file=upload.file,
        )
    finally:
        await form.close()
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return envelope(
        request, VersionUploadResponse(version=_to_version(result.version), created=result.created)
    )


@router.get(
    "/{document_id}/versions/{version_id}", summary="Inspect one version", responses=_ERRORS
)
async def get_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
) -> ResponseEnvelope[VersionResponse]:
    return envelope(request, _to_version(await service.get_version(user, document_id, version_id)))


@router.post(
    "/{document_id}/versions/{version_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-queue processing of a failed version",
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "Only failed versions can be retried"},
        429: {"model": ErrorResponse, "description": "Too many requests"},
    },
    dependencies=[Depends(enforce_retry_rate_limit)],
)
async def retry_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
) -> ResponseEnvelope[VersionResponse]:
    version = await service.retry_version(user, document_id, version_id)
    return envelope(request, _to_version(version))


@router.post(
    "/{document_id}/versions/{version_id}/activate",
    summary="Make a retained, ready version the active one (restore)",
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "Only a ready version can be activated"},
        429: {"model": ErrorResponse, "description": "Too many requests"},
    },
    dependencies=[Depends(enforce_retry_rate_limit)],
)
async def activate_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    service: DocumentServiceDep,
) -> ResponseEnvelope[VersionResponse]:
    version = await service.activate_version(user, document_id, version_id)
    return envelope(request, _to_version(version))


@router.delete(
    "/{document_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one retained version (not the active or the only one)",
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "The active or only version"},
    },
)
async def delete_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    service: DocumentServiceDep,
) -> Response:
    await service.delete_version(user, document_id, version_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
