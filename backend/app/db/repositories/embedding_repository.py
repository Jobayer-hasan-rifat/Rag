import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings.types import EmbeddingConfig
from app.models.chunk_embedding import ChunkEmbedding
from app.models.document import Document, DocumentCollection
from app.models.document_chunk import DocumentChunk
from app.models.document_section import DocumentSection
from app.models.embedding_config import EmbeddingConfigRecord

CHUNK_CONFIG_CONSTRAINT = "uq_chunk_embeddings_chunk_config"


@dataclass(frozen=True)
class PendingChunk:
    """A chunk that has no (current) vector under the active configuration."""

    id: uuid.UUID
    chunk_index: int
    text: str
    text_sha256: str
    heading_path: tuple[str, ...]


@dataclass(frozen=True)
class VectorRow:
    chunk_id: uuid.UUID
    vector: list[float]
    text_sha256: str
    truncated: bool


@dataclass(frozen=True)
class DenseCandidate:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    distance: float


@dataclass(frozen=True)
class ChunkRecord:
    """A chunk with its source location (no score)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    chunk_index: int
    section_ordinal: int
    page_number: int | None
    heading: str | None
    heading_level: int | None
    heading_path: tuple[str, ...]
    text: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    chunk_index: int
    section_ordinal: int
    page_number: int | None
    heading: str | None
    heading_level: int | None
    heading_path: tuple[str, ...]
    text: str
    start_char: int
    end_char: int
    distance: float


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- configuration -----------------------------------------------------------------

    async def ensure_config(self, config: EmbeddingConfig) -> uuid.UUID:
        """The id of the stored row for this configuration, creating it on first use."""
        values = {
            "fingerprint": config.fingerprint(),
            "provider": config.provider,
            "model": config.model,
            "revision": config.revision,
            "dimension": config.dimension,
            "metric": config.metric,
            "normalized": config.normalized,
            "query_prefix": config.query_prefix,
            "passage_prefix": config.passage_prefix,
            "max_seq_length": config.max_seq_length,
            "include_heading": config.include_heading,
            "preprocessing_version": config.preprocessing_version,
        }
        statement = (
            pg_insert(EmbeddingConfigRecord)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["fingerprint"], set_={"fingerprint": values["fingerprint"]}
            )
            .returning(EmbeddingConfigRecord.id)
        )
        return (await self._session.execute(statement)).scalar_one()

    # --- writing vectors ---------------------------------------------------------------

    async def drop_stale(self, document_id: uuid.UUID, config_id: uuid.UUID) -> int:
        """Delete vectors whose chunk text no longer matches what was embedded."""
        stale = (
            select(ChunkEmbedding.id)
            .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
            .where(
                ChunkEmbedding.document_id == document_id,
                ChunkEmbedding.config_id == config_id,
                ChunkEmbedding.text_sha256 != DocumentChunk.text_sha256,
            )
        )
        result = await self._session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.id.in_(stale))
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def next_batch(
        self, document_id: uuid.UUID, config_id: uuid.UUID, limit: int
    ) -> list[PendingChunk]:
        result = await self._session.execute(
            select(
                DocumentChunk.id,
                DocumentChunk.chunk_index,
                DocumentChunk.text,
                DocumentChunk.text_sha256,
                DocumentChunk.heading_path,
            )
            .outerjoin(
                ChunkEmbedding,
                and_(
                    ChunkEmbedding.chunk_id == DocumentChunk.id,
                    ChunkEmbedding.config_id == config_id,
                ),
            )
            .where(DocumentChunk.document_id == document_id, ChunkEmbedding.id.is_(None))
            .order_by(DocumentChunk.chunk_index)
            .limit(limit)
        )
        return [
            PendingChunk(row[0], row[1], row[2], row[3], tuple(row[4] or ()))
            for row in result.all()
        ]

    async def insert_batch(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        config_id: uuid.UUID,
        rows: Sequence[VectorRow],
    ) -> int:
        """Insert vectors; a chunk that already has one under this configuration is skipped."""
        if not rows:
            return 0
        statement = (
            pg_insert(ChunkEmbedding)
            .values(
                [
                    {
                        "chunk_id": row.chunk_id,
                        "document_id": document_id,
                        "user_id": user_id,
                        "config_id": config_id,
                        "embedding": row.vector,
                        "text_sha256": row.text_sha256,
                        "truncated": row.truncated,
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_nothing(constraint=CHUNK_CONFIG_CONSTRAINT)
        )
        result = await self._session.execute(statement)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def count_for(self, document_id: uuid.UUID, config_id: uuid.UUID) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(ChunkEmbedding)
                    .where(
                        ChunkEmbedding.document_id == document_id,
                        ChunkEmbedding.config_id == config_id,
                    )
                )
            ).scalar_one()
        )

    async def truncated_count(self, document_id: uuid.UUID, config_id: uuid.UUID) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(ChunkEmbedding)
                    .where(
                        ChunkEmbedding.document_id == document_id,
                        ChunkEmbedding.config_id == config_id,
                        ChunkEmbedding.truncated.is_(True),
                    )
                )
            ).scalar_one()
        )

    async def delete_other_configs(self, document_id: uuid.UUID, keep_config_id: uuid.UUID) -> int:
        """Drop a document's vectors from earlier configurations once the new set is complete."""
        result = await self._session.execute(
            delete(ChunkEmbedding).where(
                ChunkEmbedding.document_id == document_id,
                ChunkEmbedding.config_id != keep_config_id,
            )
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # --- search ------------------------------------------------------------------------

    @staticmethod
    def _nearest(
        *,
        user_id: uuid.UUID,
        config_id: uuid.UUID,
        vector: list[float],
        limit: int,
        document_ids: Sequence[uuid.UUID] | None,
        collection_id: uuid.UUID | None,
    ) -> Any:
        """The nearest vectors as a subquery over the vector table alone.

        Only the caller's own vectors under the active configuration, of documents that are
        `ready`, are considered. Chunk text and sections are joined afterwards for the `limit`
        survivors only (joining first made the search 8 times slower, measured).
        """
        distance = ChunkEmbedding.embedding.cosine_distance(vector)
        ready_documents = select(Document.id).where(
            Document.user_id == user_id, Document.status == "ready"
        )
        inner = select(
            ChunkEmbedding.chunk_id.label("chunk_id"),
            ChunkEmbedding.document_id.label("document_id"),
            distance.label("distance"),
        ).where(
            ChunkEmbedding.user_id == user_id,
            ChunkEmbedding.config_id == config_id,
            ChunkEmbedding.document_id.in_(ready_documents),
        )
        if document_ids:
            inner = inner.where(ChunkEmbedding.document_id.in_(list(document_ids)))
        if collection_id is not None:
            inner = inner.where(
                ChunkEmbedding.document_id.in_(
                    select(DocumentCollection.document_id).where(
                        DocumentCollection.collection_id == collection_id,
                        DocumentCollection.user_id == user_id,
                    )
                )
            )
        return inner.order_by(distance).limit(limit).subquery("nearest")

    async def nearest_candidates(
        self,
        *,
        user_id: uuid.UUID,
        config_id: uuid.UUID,
        vector: list[float],
        limit: int,
        document_ids: Sequence[uuid.UUID] | None = None,
        collection_id: uuid.UUID | None = None,
        ef_search: int | None = None,
    ) -> list[DenseCandidate]:
        """Dense candidate ids and distances only (no chunk text), best first."""
        if ef_search is not None:
            await self._session.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
            await self._session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        nearest = self._nearest(
            user_id=user_id,
            config_id=config_id,
            vector=vector,
            limit=limit,
            document_ids=document_ids,
            collection_id=collection_id,
        )
        result = await self._session.execute(
            select(nearest.c.chunk_id, nearest.c.document_id, nearest.c.distance).order_by(
                nearest.c.distance, nearest.c.chunk_id
            )
        )
        return [DenseCandidate(row[0], row[1], float(row[2])) for row in result.all()]

    async def hydrate(
        self, *, user_id: uuid.UUID, chunk_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, ChunkRecord]:
        """Chunk text and source metadata for chosen chunks.

        The ownership and `ready` checks are repeated here, so a chunk that a candidate query
        should never have returned still cannot be shown.
        """
        if not chunk_ids:
            return {}
        result = await self._session.execute(
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                Document.filename,
                DocumentChunk.chunk_index,
                DocumentSection.ordinal,
                DocumentChunk.page_number,
                DocumentChunk.heading,
                DocumentChunk.heading_level,
                DocumentChunk.heading_path,
                DocumentChunk.text,
                DocumentChunk.start_char,
                DocumentChunk.end_char,
            )
            .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.id.in_(list(chunk_ids)),
                DocumentChunk.user_id == user_id,
                Document.user_id == user_id,
                Document.status == "ready",
            )
        )
        return {
            row[0]: ChunkRecord(
                chunk_id=row[0],
                document_id=row[1],
                document_name=row[2],
                chunk_index=row[3],
                section_ordinal=row[4],
                page_number=row[5],
                heading=row[6],
                heading_level=row[7],
                heading_path=tuple(row[8] or ()),
                text=row[9],
                start_char=row[10],
                end_char=row[11],
            )
            for row in result.all()
        }

    async def search(
        self,
        *,
        user_id: uuid.UUID,
        config_id: uuid.UUID,
        vector: list[float],
        top_k: int,
        document_ids: Sequence[uuid.UUID] | None = None,
        collection_id: uuid.UUID | None = None,
        ef_search: int | None = None,
    ) -> list[SearchHit]:
        """The `top_k` nearest chunks among the caller's own, searchable (ready) documents.

        Ownership is enforced twice: the vector rows carry the owner (a database-enforced copy of
        the document's owner) and the document join is restricted to the same user.
        """
        if ef_search is not None:
            await self._session.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
            await self._session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        nearest = self._nearest(
            user_id=user_id,
            config_id=config_id,
            vector=vector,
            limit=top_k,
            document_ids=document_ids,
            collection_id=collection_id,
        )
        statement = (
            select(
                nearest.c.chunk_id,
                nearest.c.document_id,
                Document.filename,
                DocumentChunk.chunk_index,
                DocumentSection.ordinal,
                DocumentChunk.page_number,
                DocumentChunk.heading,
                DocumentChunk.heading_level,
                DocumentChunk.heading_path,
                DocumentChunk.text,
                DocumentChunk.start_char,
                DocumentChunk.end_char,
                nearest.c.distance,
            )
            .join(DocumentChunk, DocumentChunk.id == nearest.c.chunk_id)
            .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
            .join(
                Document,
                and_(Document.id == nearest.c.document_id, Document.user_id == user_id),
            )
            .order_by(nearest.c.distance)
        )
        result = await self._session.execute(statement)
        return [
            SearchHit(
                chunk_id=row[0],
                document_id=row[1],
                document_name=row[2],
                chunk_index=row[3],
                section_ordinal=row[4],
                page_number=row[5],
                heading=row[6],
                heading_level=row[7],
                heading_path=tuple(row[8] or ()),
                text=row[9],
                start_char=row[10],
                end_char=row[11],
                distance=float(row[12]),
            )
            for row in result.all()
        ]

    async def config_id_for(self, config: EmbeddingConfig) -> uuid.UUID | None:
        """The stored id of this configuration, or None if nothing was ever embedded with it."""
        return (
            await self._session.execute(
                select(EmbeddingConfigRecord.id).where(
                    EmbeddingConfigRecord.fingerprint == config.fingerprint()
                )
            )
        ).scalar_one_or_none()
