import asyncio
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.documents.normalization import normalize_text
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository, SearchHit
from app.embeddings.types import EmbeddingConfig, EmbeddingError, EmbeddingProvider, Vectors
from app.exceptions import (
    EmbeddingUnavailableError,
    NotFoundError,
    RequestValidationFailedError,
)
from app.models.user import User
from app.observability.logging import get_logger

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
        self._gate = gate
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
        text = normalize_text(query)
        if not text:
            raise RequestValidationFailedError("The query must contain some text")
        if len(text) > settings.search_max_query_chars:
            raise RequestValidationFailedError(
                f"The query is longer than {settings.search_max_query_chars} characters"
            )
        limit = top_k or settings.search_default_top_k
        if limit > settings.search_max_top_k:
            raise RequestValidationFailedError(f"top_k must be at most {settings.search_max_top_k}")

        wanted = list(dict.fromkeys(document_ids or []))
        if wanted and await self._documents.owned_ids(actor_id, wanted) != set(wanted):
            raise NotFoundError("One or more documents were not found")
        if collection_id is not None and not await self._collections.owned_ids(
            actor_id, [collection_id]
        ):
            raise NotFoundError("Collection not found")

        # Authentication and the checks above left a transaction (and a pooled connection) open.
        # Release it: the model call may wait for a slot and then run for a while.
        await self._session.rollback()
        embed_started = time.monotonic()
        vector = await self._embed_query(text)
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

    def _release_when_done(self, future: "asyncio.Future[Vectors]") -> None:
        """Free the concurrency slot, and consume the result so an abandoned call that fails
        later does not log "exception was never retrieved"."""
        self._gate.release()
        if not future.cancelled():
            future.exception()

    async def _embed_query(self, text: str) -> Vectors:
        """Embed in a worker thread with bounded concurrency and a time limit.

        The concurrency slot is held until the thread really finishes, even if the caller gave
        up, so abandoned requests cannot pile up work behind the model.
        """
        settings = self._settings
        timeout = settings.search_query_timeout_seconds
        try:
            await asyncio.wait_for(self._gate.acquire(), timeout=timeout)
        except TimeoutError:
            logger.warning("semantic search is saturated")
            raise EmbeddingUnavailableError("Semantic search is busy; try again shortly") from None
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, self._provider.embed_query, text)
        future.add_done_callback(self._release_when_done)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError:
            logger.warning("query embedding timed out")
            raise EmbeddingUnavailableError() from None
        except EmbeddingError as error:
            logger.error(
                "query embedding failed",
                extra={"error_type": type(error).__name__, "retryable": error.retryable},
            )
            raise EmbeddingUnavailableError() from None
