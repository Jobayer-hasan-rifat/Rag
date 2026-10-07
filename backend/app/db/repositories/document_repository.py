import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.models.document import Document, DocumentCollection

SortField = Literal["created_at", "filename", "file_size"]
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
