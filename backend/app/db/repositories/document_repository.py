import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, case, delete, exists, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.chunk_embedding import ChunkEmbedding
from app.models.collection import Collection
from app.models.document import Document, DocumentCollection, DocumentFamily
from app.models.document_chunk import DocumentChunk

SortField = Literal["created_at", "filename", "file_size"]
IN_PROGRESS = ("parsing", "chunking")
# Vectors belong to chunks: whenever chunks are replaced or dropped the embedding bookkeeping on
# the document starts over (the vectors themselves go with the chunks by cascade).
_EMBEDDING_RESET: dict[str, Any] = {
    "embedding_attempts": 0,
    "embedding_started_at": None,
    "embedding_updated_at": None,
    "embedding_completed_at": None,
    "embedded_chunk_count": None,
    "embedding_config_id": None,
}
SortOrder = Literal["asc", "desc"]


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class DocumentFilters:
    status: str | None = None
    file_type: str | None = None
    collection_id: uuid.UUID | None = None
    search: str | None = None


@dataclass(frozen=True)
class ClaimedDocument:
    id: uuid.UUID
    storage_key: str
    file_type: str
    file_size: int
    attempts: int


@dataclass(frozen=True)
class ClaimedEmbedding:
    id: uuid.UUID
    user_id: uuid.UUID
    chunk_count: int
    attempts: int


@dataclass(frozen=True)
class CollectionRef:
    id: uuid.UUID
    name: str


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # whether the latest `complete_embedding` call made its version the active one
        self.last_activation = False

    async def add(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def lock_existing_versions(
        self, user_id: uuid.UUID, ids: set[uuid.UUID]
    ) -> set[uuid.UUID]:
        """The caller's document versions among `ids` that still exist, locked against deletion
        until the transaction ends (a concurrent delete waits, then scrubs what was stored
        meanwhile)."""
        if not ids:
            return set()
        result = await self._session.execute(
            select(Document.id)
            .where(Document.user_id == user_id, Document.id.in_(ids))
            .with_for_update(read=True)
        )
        return set(result.scalars().all())

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def total_size(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.sum(Document.file_size), 0)).where(
                Document.user_id == user_id
            )
        )
        return int(result.scalar_one())

    async def get_by_checksum(self, user_id: uuid.UUID, checksum: str) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.user_id == user_id, Document.checksum_sha256 == checksum
            )
        )
        return result.scalar_one_or_none()

    async def list_page(
        self,
        user_id: uuid.UUID,
        *,
        filters: DocumentFilters,
        sort: SortField,
        order: SortOrder,
        page: int,
        page_size: int,
    ) -> tuple[list[Document], int]:
        conditions: list[ColumnElement[bool]] = [
            Document.user_id == user_id,
            Document.is_current.is_(True),
        ]
        if filters.status:
            conditions.append(Document.status == filters.status)
        if filters.file_type:
            conditions.append(Document.file_type == filters.file_type)
        if filters.search:
            conditions.append(
                Document.filename.ilike(f"%{escape_like(filters.search)}%", escape="\\")
            )
        if filters.collection_id:
            conditions.append(
                exists().where(
                    DocumentCollection.document_id == Document.family_id,
                    DocumentCollection.collection_id == filters.collection_id,
                )
            )
        total = (
            await self._session.execute(
                select(func.count()).select_from(Document).where(*conditions)
            )
        ).scalar_one()
        column = getattr(Document, sort)
        ordering = (
            (column.asc(), Document.id.asc())
            if order == "asc"
            else (column.desc(), Document.id.desc())
        )
        result = await self._session.execute(
            select(Document)
            .where(*conditions)
            .order_by(*ordering)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()

    async def collections_for(
        self, document_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[CollectionRef]]:
        """One query for all documents on a page (avoids N+1)."""
        grouped: dict[uuid.UUID, list[CollectionRef]] = {doc_id: [] for doc_id in document_ids}
        if not document_ids:
            return grouped
        result = await self._session.execute(
            select(DocumentCollection.document_id, Collection.id, Collection.name)
            .join(Collection, Collection.id == DocumentCollection.collection_id)
            .where(DocumentCollection.document_id.in_(document_ids))
            .order_by(func.lower(Collection.name), Collection.id)
        )
        for document_id, collection_id, name in result.all():
            grouped[document_id].append(CollectionRef(id=collection_id, name=name))
        return grouped

    async def claim_for_processing(
        self, document_id: uuid.UUID, *, stale_before: datetime
    ) -> ClaimedDocument | None:
        """Atomically move a pending (or abandoned, stale) document to `parsing`.

        The single UPDATE is the lock: of several workers handed the same task, exactly one
        gets a row back, so a document is never processed concurrently.
        """
        statement = (
            update(Document)
            .where(
                Document.id == document_id,
                (Document.status == "pending")
                | (
                    Document.status.in_(IN_PROGRESS)
                    & (Document.processing_started_at < stale_before)
                ),
            )
            .values(
                status="parsing",
                processing_started_at=func.now(),
                processing_completed_at=None,
                processing_attempts=Document.processing_attempts + 1,
                failure_reason=None,
                error_message=None,
            )
            .returning(
                Document.id,
                Document.storage_key,
                Document.file_type,
                Document.file_size,
                Document.processing_attempts,
            )
            .execution_options(synchronize_session=False)
        )
        row = (await self._session.execute(statement)).first()
        return ClaimedDocument(*row) if row else None

    async def complete_extraction(
        self,
        document_id: uuid.UUID,
        *,
        page_count: int | None,
        character_count: int,
        metadata: dict[str, Any],
    ) -> bool:
        """parsing -> chunking once the text is extracted and stored.

        Clears any earlier chunk summary: replacing the sections also cascades away old chunks.
        """
        result = await self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "parsing")
            .values(
                status="chunking",
                page_count=page_count,
                character_count=character_count,
                chunk_count=None,
                chunking_version=None,
                processing_metadata=metadata,
                failure_reason=None,
                error_message=None,
                **_EMBEDDING_RESET,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def claim_for_rechunking(self, document_id: uuid.UUID) -> ClaimedDocument | None:
        """chunked/ready -> chunking, to regenerate chunks from the stored sections."""
        statement = (
            update(Document)
            .where(Document.id == document_id, Document.status.in_(("chunked", "ready")))
            .values(
                status="chunking",
                processing_started_at=func.now(),
                processing_completed_at=None,
                processing_attempts=1,
            )
            .returning(
                Document.id,
                Document.storage_key,
                Document.file_type,
                Document.file_size,
                Document.processing_attempts,
            )
            .execution_options(synchronize_session=False)
        )
        row = (await self._session.execute(statement)).first()
        return ClaimedDocument(*row) if row else None

    async def complete_chunking(
        self,
        document_id: uuid.UUID,
        *,
        chunk_count: int,
        chunking_version: str,
        chunking_info: dict[str, Any],
    ) -> bool:
        """chunking -> chunked: chunks stored, document ready for embedding."""
        result = await self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "chunking")
            .values(
                status="chunked",
                chunk_count=chunk_count,
                chunking_version=chunking_version,
                processing_completed_at=func.now(),
                processing_metadata=Document.processing_metadata.concat(
                    type_coerce({"chunking": chunking_info}, JSONB)
                ),
                **_EMBEDDING_RESET,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def fail_processing(
        self,
        document_id: uuid.UUID,
        *,
        reason: str,
        message: str,
        started_before: datetime | None = None,
    ) -> bool:
        """parsing -> failed with a safe machine-readable reason and message."""
        conditions = [Document.id == document_id, Document.status.in_(IN_PROGRESS)]
        if started_before is not None:
            conditions.append(Document.processing_started_at < started_before)
        result = await self._session.execute(
            update(Document)
            .where(*conditions)
            .values(
                status="failed",
                failure_reason=reason,
                error_message=message,
                processing_completed_at=func.now(),
                chunk_count=None,
                chunking_version=None,
                **_EMBEDDING_RESET,
            )
            .execution_options(synchronize_session=False)
        )
        return await self._after_leaving_chunked(document_id, bool(result.rowcount))  # type: ignore[attr-defined]

    async def release_for_retry(
        self, document_id: uuid.UUID, *, started_before: datetime | None = None
    ) -> bool:
        """parsing -> pending, so the document can be claimed again."""
        conditions = [Document.id == document_id, Document.status.in_(IN_PROGRESS)]
        if started_before is not None:
            conditions.append(Document.processing_started_at < started_before)
        result = await self._session.execute(
            update(Document)
            .where(*conditions)
            .values(status="pending", chunk_count=None, chunking_version=None, **_EMBEDDING_RESET)
            .execution_options(synchronize_session=False)
        )
        return await self._after_leaving_chunked(document_id, bool(result.rowcount))  # type: ignore[attr-defined]

    async def _after_leaving_chunked(self, document_id: uuid.UUID, applied: bool) -> bool:
        """Keep the invariant "a document has chunks only while it is chunked or beyond".

        A failed or re-queued document must not keep serving the chunks of an earlier run, so
        they are removed in the same transaction as the status change.
        """
        if applied:
            await self._session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
        return applied

    async def reset_for_manual_retry(self, document_id: uuid.UUID) -> str | None:
        """failed -> chunked (the failure came after chunking) or pending, with fresh budgets.

        Returns the new status, or None if the document was not failed.
        """
        after_chunking = Document.chunk_count.is_not(None)
        result = await self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "failed")
            .values(
                status=case((after_chunking, "chunked"), else_="pending"),
                failure_reason=None,
                error_message=None,
                processing_attempts=0,
                processing_completed_at=case((after_chunking, Document.processing_completed_at)),
                embedding_attempts=0,
                embedding_started_at=None,
                embedding_updated_at=None,
                embedding_completed_at=None,
            )
            .returning(Document.status)
            .execution_options(synchronize_session=False)
        )
        row = result.first()
        return str(row[0]) if row else None

    async def find_stalled(
        self, started_before: datetime, limit: int
    ) -> list[tuple[uuid.UUID, int]]:
        result = await self._session.execute(
            select(Document.id, Document.processing_attempts)
            .where(
                Document.status.in_(IN_PROGRESS), Document.processing_started_at < started_before
            )
            .order_by(Document.processing_started_at)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def find_waiting(self, idle_since: datetime, limit: int) -> list[uuid.UUID]:
        """Pending documents nobody has claimed for a while (lost or expired queue messages)."""
        result = await self._session.execute(
            select(Document.id)
            .where(Document.status == "pending", Document.updated_at < idle_since)
            .order_by(Document.updated_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def touch(self, document_ids: list[uuid.UUID]) -> None:
        if document_ids:
            await self._session.execute(
                update(Document)
                .where(Document.id.in_(document_ids))
                .values(updated_at=func.now())
                .execution_options(synchronize_session=False)
            )

    # --- embedding stage -------------------------------------------------------------------

    async def claim_for_embedding(
        self, document_id: uuid.UUID, *, config_id: uuid.UUID, stale_before: datetime
    ) -> ClaimedEmbedding | None:
        """Atomically move a document to `embedding`; the single UPDATE is the lock.

        Claimable: a chunked document, a ready document whose vectors belong to another
        configuration (re-indexing), or an embedding document whose worker went quiet.
        """
        claimable = (
            (Document.status == "chunked")
            | (
                (Document.status == "ready")
                & Document.embedding_config_id.is_distinct_from(config_id)
            )
            | ((Document.status == "embedding") & (Document.embedding_updated_at < stale_before))
        )
        statement = (
            update(Document)
            .where(Document.id == document_id, Document.chunk_count.is_not(None), claimable)
            .values(
                status="embedding",
                embedding_started_at=func.now(),
                embedding_updated_at=func.now(),
                embedding_completed_at=None,
                embedding_attempts=Document.embedding_attempts + 1,
                failure_reason=None,
                error_message=None,
            )
            .returning(
                Document.id, Document.user_id, Document.chunk_count, Document.embedding_attempts
            )
            .execution_options(synchronize_session=False)
        )
        row = (await self._session.execute(statement)).first()
        return ClaimedEmbedding(*row) if row else None

    async def record_embedding_progress(self, document_id: uuid.UUID, *, embedded: int) -> bool:
        """Heartbeat plus progress. Progress resets the attempt counter: attempts count
        consecutive claims that achieved nothing, so a large document processed in several
        slices never exhausts its budget while a crash loop does."""
        result = await self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "embedding")
            .values(
                embedded_chunk_count=embedded,
                embedding_updated_at=func.now(),
                embedding_attempts=1,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def complete_embedding(self, document_id: uuid.UUID, *, config_id: uuid.UUID) -> bool:
        """embedding -> ready, only if every chunk really has a vector under this configuration.

        The family lock is taken before the version row changes (the order every version
        operation uses), and a version that becomes ready is activated in the same transaction
        when it is newer than the active one, so no moment shows a newer ready version that is
        neither active nor intentionally retained."""
        owner = (
            await self._session.execute(
                select(Document.family_id, Document.user_id).where(Document.id == document_id)
            )
        ).first()
        if owner is None or not await self.lock_family(owner[0], owner[1]):
            return False
        # a version that already completed under some configuration is being re-indexed: that
        # must never undo an explicit restore, so only the first readiness may activate it
        first_time = (
            await self._session.execute(
                select(Document.embedding_config_id.is_(None)).where(Document.id == document_id)
            )
        ).scalar_one_or_none()
        if not await self._mark_ready(document_id, config_id):
            return False
        self.last_activation = bool(first_time) and await self.activate_if_newer(document_id)
        return True

    async def _mark_ready(self, document_id: uuid.UUID, config_id: uuid.UUID) -> bool:
        vectors = (
            select(func.count(ChunkEmbedding.id))
            .where(ChunkEmbedding.document_id == Document.id, ChunkEmbedding.config_id == config_id)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.status == "embedding",
                Document.chunk_count == vectors,
            )
            .values(
                status="ready",
                embedding_completed_at=func.now(),
                embedding_updated_at=func.now(),
                embedding_config_id=config_id,
                embedded_chunk_count=Document.chunk_count,
                failure_reason=None,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def release_embedding(
        self, document_id: uuid.UUID, *, stale_before: datetime | None = None
    ) -> bool:
        """embedding -> chunked: out of time, a transient failure, or an abandoned worker.

        Chunks and the vectors stored so far are kept, so the next claim resumes where this
        one stopped.
        """
        conditions = [Document.id == document_id, Document.status == "embedding"]
        if stale_before is not None:
            conditions.append(Document.embedding_updated_at < stale_before)
        result = await self._session.execute(
            update(Document)
            .where(*conditions)
            .values(status="chunked", embedding_updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def fail_embedding(
        self,
        document_id: uuid.UUID,
        *,
        reason: str,
        message: str,
        stale_before: datetime | None = None,
    ) -> bool:
        """embedding -> failed. Chunks are kept: a retry resumes at `chunked`."""
        conditions = [Document.id == document_id, Document.status == "embedding"]
        if stale_before is not None:
            conditions.append(Document.embedding_updated_at < stale_before)
        result = await self._session.execute(
            update(Document)
            .where(*conditions)
            .values(
                status="failed",
                failure_reason=reason,
                error_message=message,
                embedding_completed_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def find_stalled_embedding(
        self, stale_before: datetime, limit: int
    ) -> list[tuple[uuid.UUID, int]]:
        result = await self._session.execute(
            select(Document.id, Document.embedding_attempts)
            .where(Document.status == "embedding", Document.embedding_updated_at < stale_before)
            .order_by(Document.embedding_updated_at)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def find_awaiting_embedding(self, idle_since: datetime, limit: int) -> list[uuid.UUID]:
        """Chunked documents nobody has claimed for a while (a lost or expired queue message)."""
        result = await self._session.execute(
            select(Document.id)
            .where(Document.status == "chunked", Document.updated_at < idle_since)
            .order_by(Document.updated_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def find_for_reindex(
        self, config_id: uuid.UUID, limit: int, *, include_failed: bool
    ) -> list[uuid.UUID]:
        """Ready documents embedded under another configuration (and optionally failed ones)."""
        stale = (Document.status == "ready") & Document.embedding_config_id.is_distinct_from(
            config_id
        )
        if include_failed:
            stale = stale | ((Document.status == "failed") & Document.chunk_count.is_not(None))
        result = await self._session.execute(
            select(Document.id).where(stale).order_by(Document.created_at).limit(limit)
        )
        return list(result.scalars().all())

    async def reset_failed_embedding(self, document_id: uuid.UUID) -> bool:
        """failed (after chunking) -> chunked, so a maintenance run can embed it again."""
        return await self.reset_for_manual_retry(document_id) == "chunked"

    async def embedding_state(self, document_id: uuid.UUID) -> tuple[str, int] | None:
        """(status, embedding_attempts), or None if the document no longer exists."""
        row = (
            await self._session.execute(
                select(Document.status, Document.embedding_attempts).where(
                    Document.id == document_id
                )
            )
        ).first()
        return (str(row[0]), int(row[1])) if row else None

    async def owned_ids(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Which of `ids` are documents (families) owned by `user_id` (admins get no special
        treatment)."""
        if not ids:
            return set()
        result = await self._session.execute(
            select(DocumentFamily.id).where(
                DocumentFamily.user_id == user_id, DocumentFamily.id.in_(ids)
            )
        )
        return set(result.scalars().all())

    # --- versions ---------------------------------------------------------------------------

    async def add_first_version(self, document: Document) -> Document:
        """A new document: its family takes the id of its first version (so the public document
        id and the first version id are the same value, as they are for pre-versioning data)."""
        document.id = document.id or uuid.uuid4()
        self._session.add(
            DocumentFamily(id=document.id, user_id=document.user_id, next_version_number=2)
        )
        await self._session.flush()
        document.family_id = document.id
        document.version_number = 1
        document.is_current = True
        return await self.add(document)

    async def lock_family(self, family_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Take the family's row lock (creation, activation and deletion of versions serialise
        on it); False if the family does not exist or is not the caller's."""
        row = await self._session.execute(
            select(DocumentFamily.id)
            .where(DocumentFamily.id == family_id, DocumentFamily.user_id == user_id)
            .with_for_update()
        )
        return row.scalar_one_or_none() is not None

    async def get_family(self, family_id: uuid.UUID) -> DocumentFamily | None:
        return await self._session.get(DocumentFamily, family_id)

    async def add_version(self, family_id: uuid.UUID, document: Document) -> Document:
        """A further version of a locked family. It is never active or current on arrival."""
        number = (
            await self._session.execute(
                update(DocumentFamily)
                .where(DocumentFamily.id == family_id)
                .values(
                    next_version_number=DocumentFamily.next_version_number + 1,
                    updated_at=func.now(),
                )
                .returning(DocumentFamily.next_version_number - 1)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one()
        document.family_id = family_id
        document.version_number = int(number)
        document.is_active = False
        document.is_current = False
        await self.add(document)
        await self.refresh_current(family_id)
        return document

    async def refresh_current(self, family_id: uuid.UUID) -> None:
        """The document is shown as its active version, or else its newest. Others are cleared
        first so the partial unique index never sees two current versions."""
        target = (
            await self._session.execute(
                select(Document.id)
                .where(Document.family_id == family_id)
                .order_by(Document.is_active.desc(), Document.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if target is None:
            return
        flags = {"synchronize_session": False}
        await self._session.execute(
            update(Document)
            .where(Document.family_id == family_id, Document.is_current, Document.id != target)
            .values(is_current=False)
            .execution_options(**flags)
        )
        await self._session.execute(
            update(Document)
            .where(Document.id == target, Document.is_current.is_(False))
            .values(is_current=True)
            .execution_options(**flags)
        )

    async def _switch_active(self, family_id: uuid.UUID, version_id: uuid.UUID) -> bool:
        """Make `version_id` the family's one active (and current) version, in three statements
        ordered so no moment shows two. Only a ready version is accepted (the database trigger
        enforces it too)."""
        flags = {"synchronize_session": False}
        ready = (
            await self._session.execute(
                select(Document.id)
                .where(
                    Document.id == version_id,
                    Document.family_id == family_id,
                    Document.status == "ready",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if ready is None:
            return False
        await self._session.execute(
            update(Document)
            .where(Document.family_id == family_id, Document.is_active, Document.id != version_id)
            .values(is_active=False)
            .execution_options(**flags)
        )
        await self._session.execute(
            update(Document)
            .where(Document.family_id == family_id, Document.is_current, Document.id != version_id)
            .values(is_current=False)
            .execution_options(**flags)
        )
        await self._session.execute(
            update(Document)
            .where(Document.id == version_id)
            .values(is_active=True, is_current=True)
            .execution_options(**flags)
        )
        return True

    async def activate_if_newer(self, version_id: uuid.UUID) -> bool:
        """Make a ready version active when it is newer than the active one (or none is active).

        An older version finishing late never replaces a newer active version. The family lock
        serialises this with every other activation, restore and version change.
        """
        row = (
            await self._session.execute(
                select(Document.family_id, Document.user_id, Document.version_number).where(
                    Document.id == version_id
                )
            )
        ).first()
        if row is None or not await self.lock_family(row[0], row[1]):
            return False
        active = (
            await self._session.execute(
                select(Document.version_number).where(
                    Document.family_id == row[0],
                    Document.is_active,
                    Document.status == "ready",
                )
            )
        ).scalar_one_or_none()
        if active is not None and active >= row[2]:
            await self.refresh_current(row[0])
            return False
        return await self._switch_active(row[0], version_id)

    async def restore_version(self, family_id: uuid.UUID, version_id: uuid.UUID) -> bool:
        """Explicitly make a retained ready version the active one (family already locked)."""
        return await self._switch_active(family_id, version_id)

    async def get_version(
        self, family_id: uuid.UUID, version_id: uuid.UUID, user_id: uuid.UUID
    ) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.id == version_id,
                Document.family_id == family_id,
                Document.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def current_version(self, family_id: uuid.UUID, user_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.family_id == family_id,
                Document.user_id == user_id,
                Document.is_current,
            )
        )
        return result.scalar_one_or_none()

    async def count_versions(self, family_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Document).where(Document.family_id == family_id)
        )
        return int(result.scalar_one())

    async def latest_version(self, family_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document)
            .where(Document.family_id == family_id)
            .order_by(Document.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def versions_page(
        self, family_id: uuid.UUID, user_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[Document], int]:
        conditions = [Document.family_id == family_id, Document.user_id == user_id]
        total = (
            await self._session.execute(
                select(func.count()).select_from(Document).where(*conditions)
            )
        ).scalar_one()
        result = await self._session.execute(
            select(Document)
            .where(*conditions)
            .order_by(Document.version_number.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def version_summaries(
        self, family_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, Document]]:
        """(version count, newest version) of each family, in one query for a whole page."""
        if not family_ids:
            return {}
        sibling = aliased(Document)
        count = (
            select(func.count())
            .select_from(sibling)
            .where(sibling.family_id == Document.family_id)
            .correlate(Document)
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(Document, count)
            .where(Document.family_id.in_(family_ids))
            .distinct(Document.family_id)
            .order_by(Document.family_id, Document.version_number.desc())
        )
        return {doc.family_id: (int(total), doc) for doc, total in rows.all()}

    async def storage_keys(self, family_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
        result = await self._session.execute(
            select(Document.storage_key).where(
                Document.family_id == family_id, Document.user_id == user_id
            )
        )
        return list(result.scalars().all())

    async def delete_family(self, family: DocumentFamily) -> None:
        """Delete the document with every version, chunk and vector (cascade)."""
        await self._session.delete(family)
        await self._session.flush()
