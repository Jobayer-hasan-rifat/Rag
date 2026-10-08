from enum import StrEnum

from app.exceptions import InvalidStatusTransitionError


class DocumentStatus(StrEnum):
    PENDING = "pending"  # uploaded and queued
    PARSING = "parsing"  # extracting and normalising text
    CHUNKING = "chunking"  # text extracted and stored; generating chunks
    CHUNKED = "chunked"  # chunks generated; ready for embedding (not yet searchable)
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"  # embedded and indexed: searchable
    FAILED = "failed"


# `ready` is reserved for fully processed (searchable) documents. Today the pipeline ends at
# `chunked`; the embedding and indexing stages arrive in Phase 6. Deletion is a hard delete,
# not a status.
ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.PENDING: frozenset({DocumentStatus.PARSING, DocumentStatus.FAILED}),
    # parsing/chunking may return to pending: transient-failure retry or crash recovery
    DocumentStatus.PARSING: frozenset(
        {DocumentStatus.CHUNKING, DocumentStatus.FAILED, DocumentStatus.PENDING}
    ),
    DocumentStatus.CHUNKING: frozenset(
        {DocumentStatus.CHUNKED, DocumentStatus.FAILED, DocumentStatus.PENDING}
    ),
    # chunked may be re-chunked in place (new chunking version/config) or fully reprocessed
    DocumentStatus.CHUNKED: frozenset(
        {DocumentStatus.EMBEDDING, DocumentStatus.CHUNKING, DocumentStatus.PENDING}
    ),
    DocumentStatus.EMBEDDING: frozenset({DocumentStatus.INDEXING, DocumentStatus.FAILED}),
    DocumentStatus.INDEXING: frozenset({DocumentStatus.READY, DocumentStatus.FAILED}),
    DocumentStatus.READY: frozenset({DocumentStatus.PENDING}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.PENDING}),
}


def ensure_transition(current: DocumentStatus, target: DocumentStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStatusTransitionError(
            f"Cannot change document status from '{current}' to '{target}'"
        )
