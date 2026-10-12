"""Steps shared by every search endpoint: query preparation, scope checks, query embedding."""

import asyncio
import uuid

from app.config import Settings
from app.core.documents.normalization import normalize_text
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.embeddings.types import EmbeddingError, EmbeddingProvider, Vectors
from app.exceptions import (
    EmbeddingUnavailableError,
    NotFoundError,
    RequestValidationFailedError,
)
from app.observability.logging import get_logger

logger = get_logger("app.search")


def prepare_query(raw: str, settings: Settings) -> str:
    """Normalise a query like document text and enforce its length limit."""
    text = normalize_text(raw)
    if not text:
        raise RequestValidationFailedError("The query must contain some text")
    if len(text) > settings.search_max_query_chars:
        raise RequestValidationFailedError(
            f"The query is longer than {settings.search_max_query_chars} characters"
        )
    return text


def resolve_top_k(top_k: int | None, settings: Settings) -> int:
    limit = top_k or settings.search_default_top_k
    if limit > settings.search_max_top_k:
        raise RequestValidationFailedError(f"top_k must be at most {settings.search_max_top_k}")
    return limit


async def validate_scope(
    documents: DocumentRepository,
    collections: CollectionRepository,
    actor_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
    collection_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Filters may only name the caller's own documents and collections; anything else is a 404
    that looks exactly like a missing one. Returns the de-duplicated document ids."""
    wanted = list(dict.fromkeys(document_ids or []))
    if wanted and await documents.owned_ids(actor_id, wanted) != set(wanted):
        raise NotFoundError("One or more documents were not found")
    if collection_id is not None and not await collections.owned_ids(actor_id, [collection_id]):
        raise NotFoundError("Collection not found")
    return wanted


class QueryEmbedder:
    """Embeds queries in a worker thread with bounded concurrency and a time limit.

    The concurrency slot is held until the thread really finishes, even if the caller gave up,
    so abandoned requests cannot pile up work behind the model. Every search endpoint embeds
    queries through this class, so a query is always embedded exactly as the semantic endpoint
    does it (the provider adds the model's query prefix).
    """

    def __init__(
        self, provider: EmbeddingProvider, gate: asyncio.Semaphore, settings: Settings
    ) -> None:
        self._provider = provider
        self._gate = gate
        self._settings = settings

    def _release_when_done(self, future: "asyncio.Future[Vectors]") -> None:
        """Free the slot, and consume the result so an abandoned call that fails later does not
        log "exception was never retrieved"."""
        self._gate.release()
        if not future.cancelled():
            future.exception()

    async def embed(self, text: str) -> Vectors:
        timeout = self._settings.search_query_timeout_seconds
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
