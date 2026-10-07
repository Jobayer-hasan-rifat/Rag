import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.collection_repository import CollectionRepository
from app.exceptions import ConflictError, NotFoundError
from app.models.collection import Collection
from app.models.user import User
from app.observability.logging import get_logger
from app.schemas.collection import AddDocumentsResult, CollectionCreate, CollectionUpdate
from app.security.authorization import can_access_owned_resource

logger = get_logger("app.collections")


@dataclass(frozen=True)
class CollectionView:
    collection: Collection
    document_count: int


class CollectionService:
    def __init__(self, *, session: AsyncSession, collections: CollectionRepository) -> None:
        self._session = session
        self._collections = collections

    async def create(self, actor: User, data: CollectionCreate) -> CollectionView:
        collection = Collection(user_id=actor.id, name=data.name, description=data.description)
        await self._persist(collection)
        logger.info("collection created", extra={"user_id": str(actor.id)})
        return CollectionView(collection, 0)

    async def list_collections(
        self, actor: User, *, search: str | None, page: int, page_size: int
    ) -> tuple[list[CollectionView], int]:
        items, total = await self._collections.list_page(
            actor.id, search=search, page=page, page_size=page_size
        )
        counts = await self._collections.document_counts([item.id for item in items])
        return [CollectionView(item, counts[item.id]) for item in items], total

    async def get(self, actor: User, collection_id: uuid.UUID) -> CollectionView:
        collection = await self._accessible(actor, collection_id)
        counts = await self._collections.document_counts([collection.id])
        return CollectionView(collection, counts[collection.id])

    async def update(
        self, actor: User, collection_id: uuid.UUID, data: CollectionUpdate
    ) -> CollectionView:
        collection = await self._accessible(actor, collection_id)
        if "name" in data.model_fields_set and data.name is not None:
            collection.name = data.name
        if "description" in data.model_fields_set:
            collection.description = data.description
        await self._persist(collection)
        counts = await self._collections.document_counts([collection.id])
        return CollectionView(collection, counts[collection.id])

    async def delete(self, actor: User, collection_id: uuid.UUID) -> None:
        collection = await self._accessible(actor, collection_id)
        await self._collections.delete(collection)  # documents are kept; only links cascade
        await self._session.commit()
        logger.info("collection deleted", extra={"user_id": str(actor.id)})

    async def add_documents(
        self, actor: User, collection_id: uuid.UUID, document_ids: list[uuid.UUID]
    ) -> AddDocumentsResult:
        collection = await self._accessible(actor, collection_id)
        unique_ids = list(dict.fromkeys(document_ids))
        owned = await self._collections.owned_document_ids(collection.user_id, unique_ids)
        if owned != set(unique_ids):
            raise NotFoundError("One or more documents were not found")
        added = await self._collections.add_documents(collection.id, collection.user_id, unique_ids)
        await self._session.commit()
        return AddDocumentsResult(added_count=added, already_exists_count=len(unique_ids) - added)

    async def remove_document(
        self, actor: User, collection_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        collection = await self._accessible(actor, collection_id)
        if not await self._collections.link_exists(collection.id, document_id):
            raise NotFoundError("Document not found in this collection")
        await self._collections.remove_document(collection.id, document_id)
        await self._session.commit()

    async def _accessible(self, actor: User, collection_id: uuid.UUID) -> Collection:
        """Missing and not-yours are indistinguishable, so ids cannot be probed."""
        collection = await self._collections.get(collection_id)
        if collection is None or not can_access_owned_resource(
            actor_id=actor.id, actor_role=actor.role_name, owner_id=collection.user_id
        ):
            raise NotFoundError("Collection not found")
        return collection

    async def _persist(self, collection: Collection) -> None:
        try:
            if collection.id is None:
                await self._collections.add(collection)
            else:
                await self._session.flush()
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise ConflictError("A collection with this name already exists") from None
        await self._session.refresh(collection)
