import asyncio
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository, SearchHit
from app.embeddings.types import EmbeddingConfig, EmbeddingProvider
from app.models.user import User
from app.observability.logging import get_logger
from app.services.search_common import QueryEmbedder, prepare_query, resolve_top_k, validate_scope

logger = get_logger("app.search")


@dataclass(frozen=True)
class SemanticResult:
    hit: SearchHit
    rank: int
    score: float


@dataclass(frozen=True)
class SemanticOutcome:
    results: list[SemanticResult]
    top_k: int
    config: EmbeddingConfig
    latency_ms: int


class SearchService:
    """Semantic (vector) search over the caller's own documents.

    The query is normalised like document text, embedded with the same configuration the
    documents were embedded with, and matched with pgvector's cosine distance. Only the caller's
    own, searchable (`ready`) documents are ever considered.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        documents: DocumentRepository,
        collections: CollectionRepository,
        embeddings: EmbeddingRepository,
        provider: EmbeddingProvider,
        gate: asyncio.Semaphore,
        settings: Settings,
    ) -> None:
        self._session = session
        self._documents = documents
        self._collections = collections
        self._embeddings = embeddings
        self._provider = provider
        self._embedder = QueryEmbedder(provider, gate, settings)
        self._settings = settings

    async def semantic(
        self,
        actor: User,
        *,
        query: str,
        top_k: int | None,
        document_ids: list[uuid.UUID] | None,
        collection_id: uuid.UUID | None,
        min_score: float | None,
    ) -> SemanticOutcome:
        started = time.monotonic()
        settings = self._settings
        actor_id = actor.id
        text = prepare_query(query, settings)
        limit = resolve_top_k(top_k, settings)
        wanted = await validate_scope(
            self._documents, self._collections, actor_id, document_ids, collection_id
        )

        # Authentication and the checks above left a transaction (and a pooled connection) open.
        # Release it: the model call may wait for a slot and then run for a while.
        await self._session.rollback()
        embed_started = time.monotonic()
        vector = await self._embedder.embed(text)
        embed_ms = round((time.monotonic() - embed_started) * 1000)

        config = self._provider.config
        config_id = await self._embeddings.config_id_for(config)
        hits: list[SearchHit] = []
        if config_id is not None:
            hits = await self._embeddings.search(
                user_id=actor_id,
                config_id=config_id,
                vector=vector.tolist(),
                top_k=limit,
                document_ids=wanted or None,
                collection_id=collection_id,
                ef_search=settings.search_hnsw_ef_search,
            )
        results = [
            SemanticResult(hit, rank, 1.0 - hit.distance)
            for rank, hit in enumerate(hits, start=1)
            if min_score is None or 1.0 - hit.distance >= min_score
        ]
        latency_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "semantic search",
            extra={
                "top_k": limit,
                "returned": len(results),
                "query_chars": len(text),
                "filtered_documents": len(wanted),
                "filtered_collection": collection_id is not None,
                "embed_ms": embed_ms,
                "latency_ms": latency_ms,
            },
        )
        return SemanticOutcome(results, limit, config, latency_ms)
