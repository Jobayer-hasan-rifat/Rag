from enum import StrEnum

from app.exceptions import InvalidStatusTransitionError


class DocumentStatus(StrEnum):
    PENDING = "pending"  # uploaded and queued
    PARSING = "parsing"  # extracting and normalising text
    CHUNKING = "chunking"  # text extracted and stored; generating chunks
    CHUNKED = "chunked"  # chunks generated; awaiting embedding (not yet searchable)
    EMBEDDING = "embedding"  # generating and storing vectors
    INDEXING = "indexing"  # reserved
    READY = "ready"  # embedded: searchable
    FAILED = "failed"


# `ready` means embedded and searchable: every chunk of the document has a vector under the active
# embedding configuration. `indexing` is reserved (for example for a keyword index); the vector
# index is maintained by PostgreSQL, so `embedding -> ready` is the normal path. Deletion is a hard
# delete, not a status.
ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.PENDING: frozenset({DocumentStatus.PARSING, DocumentStatus.FAILED}),
    # parsing/chunking may return to pending: transient-failure retry or crash recovery
    DocumentStatus.PARSING: frozenset(
        {DocumentStatus.CHUNKING, DocumentStatus.FAILED, DocumentStatus.PENDING}
    ),
    DocumentStatus.CHUNKING: frozenset(
        {DocumentStatus.CHUNKED, DocumentStatus.FAILED, DocumentStatus.PENDING}
    ),
    # chunked may be embedded, re-chunked in place (new chunking version/config) or reprocessed
    DocumentStatus.CHUNKED: frozenset(
        {DocumentStatus.EMBEDDING, DocumentStatus.CHUNKING, DocumentStatus.PENDING}
    ),
    # embedding may return to chunked: transient-failure retry or crash recovery (chunks are kept)
    DocumentStatus.EMBEDDING: frozenset(
        {
            DocumentStatus.READY,
            DocumentStatus.INDEXING,
            DocumentStatus.FAILED,
            DocumentStatus.CHUNKED,
        }
    ),
    DocumentStatus.INDEXING: frozenset({DocumentStatus.READY, DocumentStatus.FAILED}),
    # a ready document can be re-embedded (new model) or re-chunked (which drops its vectors)
    DocumentStatus.READY: frozenset(
        {DocumentStatus.PENDING, DocumentStatus.EMBEDDING, DocumentStatus.CHUNKING}
    ),
    # a manual retry resumes at `chunked` when the failure happened after chunking
    DocumentStatus.FAILED: frozenset({DocumentStatus.PENDING, DocumentStatus.CHUNKED}),
}


def ensure_transition(current: DocumentStatus, target: DocumentStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStatusTransitionError(
            f"Cannot change document status from '{current}' to '{target}'"
        )
