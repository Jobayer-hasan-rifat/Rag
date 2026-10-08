import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, delete, exists, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.models.document import Document, DocumentCollection
from app.models.document_chunk import DocumentChunk

SortField = Literal["created_at", "filename", "file_size"]
IN_PROGRESS = ("parsing", "chunking")
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
class CollectionRef:
    id: uuid.UUID
    name: str


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

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
        conditions: list[ColumnElement[bool]] = [Document.user_id == user_id]
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
                    DocumentCollection.document_id == Document.id,
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
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def claim_for_rechunking(self, document_id: uuid.UUID) -> ClaimedDocument | None:
        """chunked -> chunking, to regenerate chunks from the stored sections."""
        statement = (
            update(Document)
            .where(Document.id == document_id, Document.status == "chunked")
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
            .values(status="pending", chunk_count=None, chunking_version=None)
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

    async def reset_for_manual_retry(self, document_id: uuid.UUID) -> bool:
        """failed -> pending with a fresh attempt budget."""
        result = await self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "failed")
            .values(
                status="pending",
                failure_reason=None,
                error_message=None,
                processing_attempts=0,
                processing_completed_at=None,
                chunk_count=None,
                chunking_version=None,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

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
