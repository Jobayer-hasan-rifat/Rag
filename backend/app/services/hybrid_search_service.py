import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import (
    ChunkRecord,
    DenseCandidate,
    EmbeddingRepository,
)
from app.db.repositories.lexical_repository import LexicalCandidate, LexicalRepository
from app.embeddings.types import EmbeddingConfig, EmbeddingProvider
from app.exceptions import EmbeddingUnavailableError, ServiceUnavailableError
from app.models.user import User
from app.observability.logging import get_logger
from app.retrieval.fusion import overlap, reciprocal_rank_fusion
from app.retrieval.lexical_query import parse_lexical_query
from app.retrieval.stopwords import STOPWORDS
from app.services.search_common import QueryEmbedder, prepare_query, resolve_top_k, validate_scope

logger = get_logger("app.search")

Retriever = Literal["dense", "sparse"]
DENSE: Retriever = "dense"
SPARSE: Retriever = "sparse"


@dataclass(frozen=True)
class HybridResult:
    record: ChunkRecord
    rank: int
    score: float  # the Reciprocal Rank Fusion score: not a probability, not a similarity
    dense_rank: int | None
    sparse_rank: int | None
    dense_score: float | None  # cosine similarity, informational


@dataclass(frozen=True)
class HybridDiagnostics:
    dense_candidates: int
    sparse_candidates: int
    overlap: int
    retrievers_used: tuple[str, ...]
    degraded: tuple[str, ...]
    embed_ms: int
    dense_ms: int
    sparse_ms: int
    fuse_ms: int


@dataclass(frozen=True)
class HybridOutcome:
    results: list[HybridResult]
    top_k: int
    config: EmbeddingConfig
    rrf_k: int
    diagnostics: HybridDiagnostics
    latency_ms: int


@dataclass
class _Candidates:
    dense: list[DenseCandidate] = field(default_factory=list)
    sparse: list[LexicalCandidate] = field(default_factory=list)


class HybridSearchService:
    """Dense (pgvector) plus sparse (PostgreSQL full-text) retrieval fused with RRF.

    Each retriever is a separate database query that enforces ownership and the `ready` status
    itself; fusion works on ranks only; the final chunks are fetched with a third query that
    checks ownership once more. If one retriever is unavailable the other still answers (the
    response says which); if both fail the request fails.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        documents: DocumentRepository,
        collections: CollectionRepository,
        embeddings: EmbeddingRepository,
        lexical: LexicalRepository,
        provider: EmbeddingProvider,
        gate: asyncio.Semaphore,
        settings: Settings,
    ) -> None:
        self._session = session
        self._documents = documents
        self._collections = collections
        self._embeddings = embeddings
        self._lexical = lexical
        self._provider = provider
        self._embedder = QueryEmbedder(provider, gate, settings)
        self._settings = settings

    async def search(
        self,
        actor: User,
        *,
        query: str,
        top_k: int | None,
        document_ids: list[uuid.UUID] | None,
        collection_id: uuid.UUID | None,
        retrievers: frozenset[Retriever] = frozenset({DENSE, SPARSE}),
    ) -> HybridOutcome:
        started = time.monotonic()
        settings = self._settings
        actor_id = actor.id
        text = prepare_query(query, settings)
        limit = resolve_top_k(top_k, settings)
        wanted = await validate_scope(
            self._documents, self._collections, actor_id, document_ids, collection_id
        )
        await self._session.rollback()  # free the connection before any slow step

        candidates = _Candidates()
        degraded: list[str] = []
        timings = {"embed": 0, "dense": 0, "sparse": 0}

        if DENSE in retrievers:
            try:
                candidates.dense = await self._dense(actor_id, text, wanted, collection_id, timings)
            except EmbeddingUnavailableError:
                degraded.append(DENSE)
            except SQLAlchemyError as error:
                await self._session.rollback()
                logger.error("dense retrieval failed", extra={"error_type": type(error).__name__})
                degraded.append(DENSE)
        if SPARSE in retrievers:
            try:
                sparse_started = time.monotonic()
                candidates.sparse = await self._lexical.candidates(
                    user_id=actor_id,
                    query=parse_lexical_query(text),
                    limit=settings.hybrid_sparse_candidates,
                    max_terms=settings.sparse_max_terms,
                    document_ids=wanted or None,
                    collection_id=collection_id,
                    stopwords=STOPWORDS if settings.sparse_remove_stopwords else frozenset(),
                    timeout_ms=settings.sparse_statement_timeout_ms,
                )
                timings["sparse"] = round((time.monotonic() - sparse_started) * 1000)
            except SQLAlchemyError as error:
                await self._session.rollback()
                logger.error("sparse retrieval failed", extra={"error_type": type(error).__name__})
                degraded.append(SPARSE)

        if len(degraded) == len(retrievers):
            raise EmbeddingUnavailableError() if degraded == [DENSE] else ServiceUnavailableError()

        fuse_started = time.monotonic()
        dense_ids = [c.chunk_id for c in candidates.dense]
        sparse_ids = [c.chunk_id for c in candidates.sparse]
        weights: dict[str, float] = {
            DENSE: settings.hybrid_dense_weight,
            SPARSE: settings.hybrid_sparse_weight,
        }
        rankings: dict[str, list[uuid.UUID]] = {DENSE: dense_ids, SPARSE: sparse_ids}
        fused = reciprocal_rank_fusion(
            {name: ids for name, ids in rankings.items() if weights[name] > 0},
            k=settings.hybrid_rrf_k,
            weights=weights,
            limit=limit,
        )
        fuse_ms = round((time.monotonic() - fuse_started) * 1000)

        records = await self._embeddings.hydrate(
            user_id=actor_id, chunk_ids=[item.key for item in fused]
        )
        distance = {c.chunk_id: c.distance for c in candidates.dense}
        results: list[HybridResult] = []
        for item in fused:
            record = records.get(item.key)
            if record is None:  # no longer ready or not the caller's: never shown
                continue
            similarity = 1.0 - distance[item.key] if item.key in distance else None
            results.append(
                HybridResult(
                    record=record,
                    rank=len(results) + 1,
                    score=item.score,
                    dense_rank=item.ranks.get(DENSE),
                    sparse_rank=item.ranks.get(SPARSE),
                    dense_score=similarity,
                )
            )
        diagnostics = HybridDiagnostics(
            dense_candidates=len(dense_ids),
            sparse_candidates=len(sparse_ids),
            overlap=overlap(dense_ids, sparse_ids),
            retrievers_used=tuple(
                r for r in (DENSE, SPARSE) if r in retrievers and r not in degraded
            ),
            degraded=tuple(degraded),
            embed_ms=timings["embed"],
            dense_ms=timings["dense"],
            sparse_ms=timings["sparse"],
            fuse_ms=fuse_ms,
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "hybrid search",
            extra={
                "top_k": limit,
                "returned": len(results),
                "query_chars": len(text),
                "dense_candidates": diagnostics.dense_candidates,
                "sparse_candidates": diagnostics.sparse_candidates,
                "overlap": diagnostics.overlap,
                "degraded": ",".join(degraded),
                "filtered_documents": len(wanted),
                "filtered_collection": collection_id is not None,
                "embed_ms": diagnostics.embed_ms,
                "dense_ms": diagnostics.dense_ms,
                "sparse_ms": diagnostics.sparse_ms,
                "fuse_ms": fuse_ms,
                "latency_ms": latency_ms,
            },
        )
        return HybridOutcome(
            results, limit, self._provider.config, settings.hybrid_rrf_k, diagnostics, latency_ms
        )

    async def _dense(
        self,
        user_id: uuid.UUID,
        text: str,
        wanted: list[uuid.UUID],
        collection_id: uuid.UUID | None,
        timings: dict[str, int],
    ) -> list[DenseCandidate]:
        embed_started = time.monotonic()
        vector = await self._embedder.embed(text)
        timings["embed"] = round((time.monotonic() - embed_started) * 1000)
        config_id = await self._embeddings.config_id_for(self._provider.config)
        if config_id is None:
            return []
        dense_started = time.monotonic()
        found = await self._embeddings.nearest_candidates(
            user_id=user_id,
            config_id=config_id,
            vector=vector.tolist(),
            limit=self._settings.hybrid_dense_candidates,
            document_ids=wanted or None,
            collection_id=collection_id,
            ef_search=self._settings.search_hnsw_ef_search,
        )
        timings["dense"] = round((time.monotonic() - dense_started) * 1000)
        return found
