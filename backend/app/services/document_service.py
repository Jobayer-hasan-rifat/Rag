import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.documents.lifecycle import DocumentStatus, ensure_transition
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import (
    CollectionRef,
    DocumentFilters,
    DocumentRepository,
    SortField,
    SortOrder,
)
from app.exceptions import (
    ContentUnavailableError,
    DuplicateDocumentError,
    InvalidStatusTransitionError,
    NotFoundError,
    QuotaExceededError,
    StorageUnavailableError,
    UnsupportedFileTypeError,
)
from app.models.document import Document
from app.models.user import User
from app.observability.logging import get_logger
from app.security.authorization import can_access_owned_resource
from app.security.file_validation import (
    CHUNK_SIZE,
    document_type_for_filename,
    inspect_upload,
    sanitize_filename,
)
from app.services.processing_queue import ProcessingQueue
from app.storage.base import (
    ObjectExistsError,
    ObjectNotFoundError,
    StorageError,
    StorageProvider,
    StoredObject,
    generate_storage_key,
)

logger = get_logger("app.documents")


@dataclass(frozen=True)
class DocumentView:
    document: Document
    collections: list[CollectionRef]


@dataclass(frozen=True)
class DownloadResult:
    document: Document
    chunks: AsyncIterator[bytes]


async def _read_in_chunks(file: BinaryIO) -> AsyncIterator[bytes]:
    file.seek(0)
    while chunk := await asyncio.to_thread(file.read, CHUNK_SIZE):
        yield chunk


class DocumentService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        documents: DocumentRepository,
        collections: CollectionRepository,
        storage: StorageProvider,
        processing_queue: ProcessingQueue,
        settings: Settings,
    ) -> None:
        self._session = session
        self._documents = documents
        self._collections = collections
        self._storage = storage
        self._processing_queue = processing_queue
        self._max_upload_bytes = settings.max_upload_bytes
        self._quota_bytes = settings.max_storage_bytes_per_user

    async def upload(
        self,
        *,
        actor: User,
        filename: str | None,
        content_type: str | None,
        file: BinaryIO,
        collection_ids: list[uuid.UUID],
    ) -> DocumentView:
        actor_id = actor.id  # captured now: a rollback below would expire the loaded user
        display_name = sanitize_filename(filename)
        wanted = list(dict.fromkeys(collection_ids))
        if await self._collections.owned_ids(actor_id, wanted) != set(wanted):
            raise NotFoundError("One or more collections were not found")

        inspected = await asyncio.to_thread(
            inspect_upload, file, display_name, content_type, self._max_upload_bytes
        )
        existing = await self._documents.get_by_checksum(actor_id, inspected.sha256)
        if existing is not None:
            raise DuplicateDocumentError(str(existing.id))

        used = await self._documents.total_size(actor_id)
        if used + inspected.size_bytes > self._quota_bytes:
            raise QuotaExceededError(self._quota_bytes)

        key = generate_storage_key()
        stored = await self._store(key, file)
        if stored.sha256 != inspected.sha256 or stored.size_bytes != inspected.size_bytes:
            await self._discard(key)
            logger.error("stored object does not match the validated upload")
            raise StorageUnavailableError()

        document = Document(
            user_id=actor_id,
            filename=display_name,
            storage_key=key,
            file_type=inspected.document_type.value,
            content_type=inspected.content_type,
            file_size=inspected.size_bytes,
            checksum_sha256=inspected.sha256,
        )
        try:
            await self._documents.add(document)
            await self._collections.add_to_collections(document.id, actor_id, wanted)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            await self._discard(key)
            existing = await self._documents.get_by_checksum(actor_id, inspected.sha256)
            if existing is not None:
                raise DuplicateDocumentError(str(existing.id)) from None
            raise StorageUnavailableError() from None
        except SQLAlchemyError:
            await self._session.rollback()
            await self._discard(key)
            logger.error("document record could not be saved; stored object removed")
            raise
        logger.info(
            "document uploaded", extra={"user_id": str(actor_id), "document_id": str(document.id)}
        )
        await self._processing_queue.enqueue(document.id)
        return await self._view(document)

    async def list_documents(
        self,
        actor: User,
        *,
        filters: DocumentFilters,
        sort: SortField,
        order: SortOrder,
        page: int,
        page_size: int,
    ) -> tuple[list[DocumentView], int]:
        if filters.collection_id is not None:
            await self._accessible_collection(actor, filters.collection_id)
        items, total = await self._documents.list_page(
            actor.id, filters=filters, sort=sort, order=order, page=page, page_size=page_size
        )
        links = await self._documents.collections_for([item.id for item in items])
        return [DocumentView(item, links[item.id]) for item in items], total

    async def get(self, actor: User, document_id: uuid.UUID) -> DocumentView:
        return await self._view(await self._accessible(actor, document_id))

    async def rename(self, actor: User, document_id: uuid.UUID, new_filename: str) -> DocumentView:
        document = await self._accessible(actor, document_id)
        name = sanitize_filename(new_filename)
        if document_type_for_filename(name).value != document.file_type:
            raise UnsupportedFileTypeError("The file extension must match the document's type")
        document.filename = name
        await self._session.commit()
        await self._session.refresh(document)
        return await self._view(document)

    async def download(self, actor: User, document_id: uuid.UUID) -> DownloadResult:
        document = await self._accessible(actor, document_id)
        try:
            stored_size = await self._storage.size(document.storage_key)
            if stored_size != document.file_size:
                logger.error(
                    "stored object size differs from the record",
                    extra={"document_id": str(document.id)},
                )
                raise ContentUnavailableError()
        except ObjectNotFoundError:
            logger.error(
                "storage object missing for document", extra={"document_id": str(document.id)}
            )
            raise ContentUnavailableError() from None
        except StorageError:
            logger.error(
                "storage read failed", extra={"document_id": str(document.id)}, exc_info=True
            )
            raise StorageUnavailableError() from None
        return DownloadResult(document, self._storage.open(document.storage_key))

    async def delete(self, actor: User, document_id: uuid.UUID) -> None:
        """Remove the stored object first, then the record, so a failure never hides stored data."""
        document = await self._accessible(actor, document_id)
        try:
            removed = await self._storage.delete(document.storage_key)
        except StorageError:
            logger.error(
                "storage delete failed", extra={"document_id": str(document.id)}, exc_info=True
            )
            raise StorageUnavailableError() from None
        if not removed:
            logger.error(
                "storage object was already missing during delete",
                extra={"document_id": str(document.id)},
            )
        await self._documents.delete(document)
        await self._session.commit()
        logger.info(
            "document deleted", extra={"user_id": str(actor.id), "document_id": str(document_id)}
        )

    async def retry_processing(self, actor: User, document_id: uuid.UUID) -> DocumentView:
        """Re-queue a failed document with a fresh attempt budget."""
        document = await self._accessible(actor, document_id)
        if document.status != DocumentStatus.FAILED:
            raise InvalidStatusTransitionError("Only failed documents can be retried")
        if not await self._documents.reset_for_manual_retry(document.id):
            raise InvalidStatusTransitionError("Only failed documents can be retried")
        await self._session.commit()
        await self._session.refresh(document)
        logger.info("processing retry requested", extra={"document_id": str(document.id)})
        await self._processing_queue.enqueue(document.id)
        return await self._view(document)

    async def change_status(
        self, document: Document, target: DocumentStatus, *, error_message: str | None = None
    ) -> None:
        """Pipeline hook for later phases; enforces the lifecycle state machine."""
        ensure_transition(DocumentStatus(document.status), target)
        document.status = target.value
        document.error_message = error_message if target is DocumentStatus.FAILED else None
        await self._session.commit()

    async def _accessible(self, actor: User, document_id: uuid.UUID) -> Document:
        """Missing and not-yours are indistinguishable, so ids cannot be probed."""
        document = await self._documents.get(document_id)
        if document is None or not can_access_owned_resource(
            actor_id=actor.id, actor_role=actor.role_name, owner_id=document.user_id
        ):
            raise NotFoundError("Document not found")
        return document

    async def _accessible_collection(self, actor: User, collection_id: uuid.UUID) -> None:
        collection = await self._collections.get(collection_id)
        if collection is None or collection.user_id != actor.id:
            raise NotFoundError("Collection not found")

    async def _view(self, document: Document) -> DocumentView:
        links = await self._documents.collections_for([document.id])
        return DocumentView(document, links[document.id])

    async def _store(self, key: str, file: BinaryIO) -> StoredObject:
        try:
            return await self._storage.save(key, _read_in_chunks(file))
        except ObjectExistsError:
            logger.error("generated storage key collided")
            raise StorageUnavailableError() from None
        except StorageError:
            logger.error("storage write failed", exc_info=True)
            raise StorageUnavailableError() from None

    async def _discard(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except StorageError:
            logger.error("orphaned storage object could not be removed", extra={"storage_key": key})
