from enum import StrEnum

from app.exceptions import InvalidStatusTransitionError


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


_PIPELINE = [
    DocumentStatus.PENDING,
    DocumentStatus.PARSING,
    DocumentStatus.CHUNKING,
    DocumentStatus.EMBEDDING,
    DocumentStatus.INDEXING,
    DocumentStatus.READY,
]

# Forward through the pipeline, any in-progress stage may fail, and finished (ready or
# failed) documents may be re-queued. Deletion is a hard delete, not a status.
#
# Phase 4 additions: `parsing` is the "processing" stage of the current pipeline (extract,
# normalise, persist), so it may complete straight to `ready` (the chunking, embedding and
# indexing stages arrive in later phases and will replace that shortcut) and may be released
# back to `pending` for a retry or after a worker crash.
_FORWARD = {
    stage: frozenset({_PIPELINE[index + 1], DocumentStatus.FAILED})
    for index, stage in enumerate(_PIPELINE[:-1])
}
ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    **_FORWARD,
    DocumentStatus.PARSING: _FORWARD[DocumentStatus.PARSING]
    | {DocumentStatus.READY, DocumentStatus.PENDING},
    DocumentStatus.READY: frozenset({DocumentStatus.PENDING}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.PENDING}),
}


def ensure_transition(current: DocumentStatus, target: DocumentStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStatusTransitionError(
            f"Cannot change document status from '{current}' to '{target}'"
        )
