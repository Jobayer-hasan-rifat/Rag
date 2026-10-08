import uuid
from typing import Protocol


class ProcessingQueue(Protocol):
    """Hands a document to the background worker. Implemented by the Celery dispatcher."""

    async def enqueue(self, document_id: uuid.UUID) -> bool:
        """Queue processing; returns False if the broker could not be reached.

        Failure is not an error for the caller: the document stays `pending` and the
        recovery sweep re-queues it.
        """
        ...
