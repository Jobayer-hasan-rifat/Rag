import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.documents.lifecycle import DocumentStatus, ensure_transition
from app.db.repositories.chunk_repository import ChunkRepository
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.conversation_repository import ConversationRepository
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
    VersionLimitError,
    VersionStateError,
)
from app.models.document import Document, DocumentFamily
from app.models.document_chunk import DocumentChunk
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
    """A document as shown to its owner: its current version, plus what the other versions say.

    `document` is a version row; the public document id is `document.family_id`."""

    document: Document
    collections: list[CollectionRef]
    version_count: int = 1
    latest: Document | None = None  # the newest version when it is not the current one


@dataclass(frozen=True)
class VersionUpload:
    version: Document
    created: bool  # False: identical content was already a version of this document


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
        chunks: ChunkRepository,
        storage: StorageProvider,
        processing_queue: ProcessingQueue,
        settings: Settings,
        conversations: ConversationRepository | None = None,
    ) -> None:
        self._session = session
        self._conversations = conversations
        self._documents = documents
        self._collections = collections
        self._chunks = chunks
        self._storage = storage
        self._processing_queue = processing_queue
        self._max_upload_bytes = settings.max_upload_bytes
        self._quota_bytes = settings.max_storage_bytes_per_user
        self._max_versions = settings.max_versions_per_document

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
            raise DuplicateDocumentError(str(existing.family_id))

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
            await self._documents.add_first_version(document)
            await self._collections.add_to_collections(document.family_id, actor_id, wanted)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            await self._discard(key)
            existing = await self._documents.get_by_checksum(actor_id, inspected.sha256)
            if existing is not None:
                raise DuplicateDocumentError(str(existing.family_id)) from None
            raise StorageUnavailableError() from None
        except SQLAlchemyError:
            await self._session.rollback()
            await self._discard(key)
            logger.error("document record could not be saved; stored object removed")
            raise
        logger.info(
            "document uploaded",
            extra={
                "user_id": str(actor_id),
                "document_id": str(document.family_id),
                "version_id": str(document.id),
                "version_number": 1,
            },
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
        family_ids = [item.family_id for item in items]
        links = await self._documents.collections_for(family_ids)
        summaries = await self._documents.version_summaries(family_ids)
        views = []
        for item in items:
            count, newest = summaries[item.family_id]
            views.append(
                DocumentView(
                    item,
                    links[item.family_id],
                    version_count=count,
                    latest=None if newest.id == item.id else newest,
                )
            )
        return views, total

    async def get(self, actor: User, document_id: uuid.UUID) -> DocumentView:
        return await self._view(await self._accessible(actor, document_id))

    async def list_chunks(
        self, actor: User, document_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[DocumentChunk, int]], int]:
        """Chunks of an accessible document, in reading order (404 for anyone else's)."""
        document = await self._accessible(actor, document_id)
        return await self._chunks.list_page(document.id, page=page, page_size=page_size)

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
        """Remove every stored object first, then the records, so a failure never hides stored
        data. Repeatable: objects that are already gone are skipped, and the family lock keeps a
        concurrent replacement from adding a version that this delete would miss."""
        document = await self._accessible(actor, document_id)
        family_id, owner_id = document.family_id, document.user_id
        if not await self._documents.lock_family(family_id, owner_id):
            raise NotFoundError("Document not found")
        for key in await self._documents.storage_keys(family_id, owner_id):
            await self._remove_object(key, family_id)
        family = await self._documents.get_family(family_id)
        if family is not None:
            await self._documents.delete_family(family)
        if self._conversations is not None:
            # stored citations of this document keep only identifiers once it is gone
            await self._conversations.scrub_document_citations(owner_id, family_id)
        await self._session.commit()
        logger.info(
            "document deleted", extra={"user_id": str(actor.id), "document_id": str(family_id)}
        )

    # --- versions ---------------------------------------------------------------------------

    async def list_versions(
        self, actor: User, document_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[Document], int]:
        """The document's versions, newest first (404 for anyone else's document)."""
        family = await self._owned_family(actor, document_id)
        return await self._documents.versions_page(
            family.id, family.user_id, page=page, page_size=page_size
        )

    async def get_version(
        self, actor: User, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> Document:
        await self._owned_family(actor, document_id)
        return await self._owned_version(actor, document_id, version_id)

    async def upload_version(
        self,
        *,
        actor: User,
        document_id: uuid.UUID,
        filename: str | None,
        content_type: str | None,
        file: BinaryIO,
    ) -> VersionUpload:
        """Upload a replacement. It becomes a new version that is processed like any upload; the
        current searchable version stays active until the new one is ready (and newer)."""
        actor_id = actor.id
        await self._owned_family(actor, document_id)
        display_name = sanitize_filename(filename)
        inspected = await asyncio.to_thread(
            inspect_upload, file, display_name, content_type, self._max_upload_bytes
        )
        same = await self._same_content(actor_id, document_id, inspected.sha256)
        if same is not None:
            return same
        if await self._documents.total_size(actor_id) + inspected.size_bytes > self._quota_bytes:
            raise QuotaExceededError(self._quota_bytes)

        key = generate_storage_key()
        stored = await self._store(key, file)
        if stored.sha256 != inspected.sha256 or stored.size_bytes != inspected.size_bytes:
            await self._discard(key)
            logger.error("stored object does not match the validated upload")
            raise StorageUnavailableError()
        version = Document(
            user_id=actor_id,
            filename=display_name,
            storage_key=key,
            file_type=inspected.document_type.value,
            content_type=inspected.content_type,
            file_size=inspected.size_bytes,
            checksum_sha256=inspected.sha256,
        )
        try:
            if not await self._documents.lock_family(document_id, actor_id):
                await self._session.rollback()
                await self._discard(key)
                raise NotFoundError("Document not found")
            if await self._documents.count_versions(document_id) >= self._max_versions:
                await self._session.rollback()
                await self._discard(key)
                raise VersionLimitError(self._max_versions)
            await self._documents.add_version(document_id, version)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            await self._discard(key)
            same = await self._same_content(actor_id, document_id, inspected.sha256)
            if same is not None:
                return same
            raise StorageUnavailableError() from None
        except SQLAlchemyError:
            await self._session.rollback()
            await self._discard(key)
            logger.error("version record could not be saved; stored object removed")
            raise
        await self._session.refresh(version)  # committed: the stored file must stay
        logger.info(
            "document version uploaded",
            extra={
                "user_id": str(actor_id),
                "document_id": str(document_id),
                "version_id": str(version.id),
                "version_number": version.version_number,
            },
        )
        await self._processing_queue.enqueue(version.id)
        return VersionUpload(version, created=True)

    async def _same_content(
        self, actor_id: uuid.UUID, document_id: uuid.UUID, sha256: str
    ) -> VersionUpload | None:
        """Identical bytes already stored for this user: the same document's version is returned
        as it is (a repeated upload is idempotent); another document's is a duplicate."""
        existing = await self._documents.get_by_checksum(actor_id, sha256)
        if existing is None:
            return None
        if existing.family_id != document_id:
            raise DuplicateDocumentError(str(existing.family_id))
        return VersionUpload(existing, created=False)

    async def retry_version(
        self, actor: User, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> Document:
        """Re-queue one failed version with a fresh attempt budget (no new version is created)."""
        await self._owned_family(actor, document_id)
        version = await self._owned_version(actor, document_id, version_id)
        if version.status != DocumentStatus.FAILED:
            raise InvalidStatusTransitionError("Only failed versions can be retried")
        await self._retry(version)
        return version

    async def activate_version(
        self, actor: User, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> Document:
        """Make a retained, ready version the active one (restore). Idempotent."""
        family = await self._owned_family(actor, document_id)
        if not await self._documents.lock_family(family.id, actor.id):
            raise NotFoundError("Document not found")
        version = await self._owned_version(actor, document_id, version_id)
        if version.status != DocumentStatus.READY:
            await self._session.rollback()
            raise VersionStateError("Only a ready version can be activated")
        if version.is_active:
            await self._session.commit()  # nothing to change; releases the family lock
            return version
        try:
            restored = await self._documents.restore_version(family.id, version.id)
        except DBAPIError:  # the database refused (the version stopped being ready meanwhile)
            await self._session.rollback()
            raise VersionStateError("Only a ready version can be activated") from None
        if not restored:
            await self._session.rollback()
            raise VersionStateError("Only a ready version can be activated")
        await self._session.commit()
        await self._session.refresh(version)
        logger.info(
            "document version activated",
            extra={
                "user_id": str(actor.id),
                "document_id": str(document_id),
                "version_id": str(version_id),
                "version_number": version.version_number,
                "reason": "restore",
            },
        )
        return version

    async def delete_version(
        self, actor: User, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        """Delete one retained version (never the active one, never the only one). Its stored
        file, chunks and vectors go with it; citations of it keep only identifiers."""
        family = await self._owned_family(actor, document_id)
        if not await self._documents.lock_family(family.id, actor.id):
            raise NotFoundError("Document not found")
        version = await self._owned_version(actor, document_id, version_id)
        if version.is_active:
            await self._session.rollback()
            raise VersionStateError("The active version cannot be deleted; activate another first")
        if await self._documents.count_versions(family.id) <= 1:
            await self._session.rollback()
            raise VersionStateError("The only version cannot be deleted; delete the document")
        number, key = version.version_number, version.storage_key
        await self._remove_object(key, family.id)
        await self._documents.delete(version)
        await self._documents.refresh_current(family.id)
        if self._conversations is not None:
            await self._conversations.scrub_version_citations(actor.id, version_id)
        await self._session.commit()
        logger.info(
            "document version deleted",
            extra={
                "user_id": str(actor.id),
                "document_id": str(document_id),
                "version_id": str(version_id),
                "version_number": number,
            },
        )

    async def retry_processing(self, actor: User, document_id: uuid.UUID) -> DocumentView:
        """Re-queue a failed document with a fresh attempt budget."""
        document = await self._accessible(actor, document_id)
        newest = await self._documents.latest_version(document.family_id)
        if newest is None or newest.status != DocumentStatus.FAILED:
            raise InvalidStatusTransitionError("Only failed documents can be retried")
        await self._retry(newest)
        return await self._view(document)

    async def _retry(self, document: Document) -> None:
        resumed_at = await self._documents.reset_for_manual_retry(document.id)
        if resumed_at is None:
            raise InvalidStatusTransitionError("Only failed documents can be retried")
        await self._session.commit()
        await self._session.refresh(document)
        logger.info(
            "processing retry requested",
            extra={
                "document_id": str(document.family_id),
                "version_id": str(document.id),
                "version_number": document.version_number,
                "resumed_at": resumed_at,
            },
        )
        if resumed_at == DocumentStatus.CHUNKED:
            # the failure came after chunking: only the embedding stage has to run again
            await self._processing_queue.enqueue_embedding(document.id)
        else:
            await self._processing_queue.enqueue(document.id)

    async def change_status(
        self, document: Document, target: DocumentStatus, *, error_message: str | None = None
    ) -> None:
        """Pipeline hook for later phases; enforces the lifecycle state machine."""
        ensure_transition(DocumentStatus(document.status), target)
        document.status = target.value
        document.error_message = error_message if target is DocumentStatus.FAILED else None
        await self._session.commit()

    async def _accessible(self, actor: User, document_id: uuid.UUID) -> Document:
        """The current version of a document the actor may access. Missing and not-yours are
        indistinguishable, so ids cannot be probed."""
        family = await self._documents.get_family(document_id)
        if family is None or not can_access_owned_resource(
            actor_id=actor.id, actor_role=actor.role_name, owner_id=family.user_id
        ):
            raise NotFoundError("Document not found")
        document = await self._documents.current_version(family.id, family.user_id)
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def _owned_family(self, actor: User, document_id: uuid.UUID) -> DocumentFamily:
        """Version operations are strictly the owner's (no administrator access)."""
        family = await self._documents.get_family(document_id)
        if family is None or family.user_id != actor.id:
            raise NotFoundError("Document not found")
        return family

    async def _owned_version(
        self, actor: User, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> Document:
        version = await self._documents.get_version(document_id, version_id, actor.id)
        if version is None:
            raise NotFoundError("Version not found")
        return version

    async def _remove_object(self, key: str, document_id: uuid.UUID) -> None:
        try:
            removed = await self._storage.delete(key)
        except StorageError:
            logger.error(
                "storage delete failed", extra={"document_id": str(document_id)}, exc_info=True
            )
            raise StorageUnavailableError() from None
        if not removed:
            logger.error(
                "storage object was already missing during delete",
                extra={"document_id": str(document_id)},
            )

    async def _accessible_collection(self, actor: User, collection_id: uuid.UUID) -> None:
        collection = await self._collections.get(collection_id)
        if collection is None or collection.user_id != actor.id:
            raise NotFoundError("Collection not found")

    async def _view(self, document: Document) -> DocumentView:
        links = await self._documents.collections_for([document.family_id])
        count, newest = (await self._documents.version_summaries([document.family_id]))[
            document.family_id
        ]
        return DocumentView(
            document,
            links[document.family_id],
            version_count=count,
            latest=None if newest.id == document.id else newest,
        )

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
