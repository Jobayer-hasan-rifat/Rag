import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.document_repository import escape_like
from app.models.collection import Collection
from app.models.document import Document, DocumentCollection


class CollectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, collection: Collection) -> Collection:
        self._session.add(collection)
        await self._session.flush()
        await self._session.refresh(collection)
        return collection

    async def get(self, collection_id: uuid.UUID) -> Collection | None:
        return await self._session.get(Collection, collection_id)

    async def delete(self, collection: Collection) -> None:
        await self._session.delete(collection)
        await self._session.flush()

    async def list_page(
        self, user_id: uuid.UUID, *, search: str | None, page: int, page_size: int
    ) -> tuple[list[Collection], int]:
        conditions = [Collection.user_id == user_id]
        if search:
            conditions.append(Collection.name.ilike(f"%{escape_like(search)}%", escape="\\"))
        total = (
            await self._session.execute(
                select(func.count()).select_from(Collection).where(*conditions)
            )
        ).scalar_one()
        result = await self._session.execute(
            select(Collection)
            .where(*conditions)
            .order_by(func.lower(Collection.name), Collection.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def owned_ids(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not ids:
            return set()
        result = await self._session.execute(
            select(Collection.id).where(Collection.user_id == user_id, Collection.id.in_(ids))
        )
        return set(result.scalars().all())

    async def document_counts(self, collection_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        counts = dict.fromkeys(collection_ids, 0)
        if not collection_ids:
            return counts
        result = await self._session.execute(
            select(DocumentCollection.collection_id, func.count())
            .where(DocumentCollection.collection_id.in_(collection_ids))
            .group_by(DocumentCollection.collection_id)
        )
        for collection_id, count in result.all():
            counts[collection_id] = count
        return counts

    async def owned_document_ids(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not ids:
            return set()
        result = await self._session.execute(
            select(Document.id).where(Document.user_id == user_id, Document.id.in_(ids))
        )
        return set(result.scalars().all())

    async def add_documents(
        self, collection_id: uuid.UUID, user_id: uuid.UUID, document_ids: list[uuid.UUID]
    ) -> int:
        """Link documents; returns how many links were newly created."""
        if not document_ids:
            return 0
        statement = (
            pg_insert(DocumentCollection)
            .values(
                [
                    {"document_id": doc_id, "collection_id": collection_id, "user_id": user_id}
                    for doc_id in document_ids
                ]
            )
            .on_conflict_do_nothing()
            .returning(DocumentCollection.document_id)
        )
        return len((await self._session.execute(statement)).all())

    async def add_to_collections(
        self, document_id: uuid.UUID, user_id: uuid.UUID, collection_ids: list[uuid.UUID]
    ) -> None:
        if not collection_ids:
            return
        await self._session.execute(
            pg_insert(DocumentCollection)
            .values(
                [
                    {"document_id": document_id, "collection_id": cid, "user_id": user_id}
                    for cid in collection_ids
                ]
            )
            .on_conflict_do_nothing()
        )

    async def link_exists(self, collection_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(DocumentCollection)
            .where(
                DocumentCollection.collection_id == collection_id,
                DocumentCollection.document_id == document_id,
            )
        )
        return result.scalar_one() > 0

    async def remove_document(self, collection_id: uuid.UUID, document_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DocumentCollection).where(
                DocumentCollection.collection_id == collection_id,
                DocumentCollection.document_id == document_id,
            )
        )
