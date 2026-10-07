import secrets
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

DEFAULT_CHUNK_SIZE = 64 * 1024


class StorageError(Exception):
    """Raised when a storage backend fails."""


class ObjectNotFoundError(StorageError):
    """Raised when a requested object does not exist."""


class ObjectExistsError(StorageError):
    """Raised when saving would overwrite an existing object."""


class InvalidStorageKeyError(StorageError):
    """Raised for a key that is not a well-formed, server-generated identifier."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


def generate_storage_key(prefix: str = "documents") -> str:
    """Opaque, unguessable, collision-resistant key. Never derived from user input."""
    token = secrets.token_hex(16)
    return f"{prefix}/{token[:2]}/{token}"


class StorageProvider(ABC):
    """Backend-agnostic object storage (local filesystem now; S3-compatible later).

    Keys are server-generated identifiers; implementations must reject anything else and
    must never derive paths from user-supplied file names.
    """

    @abstractmethod
    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> StoredObject:
        """Stream `chunks` into a new object; raises ObjectExistsError instead of overwriting."""

    @abstractmethod
    def open(self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> AsyncIterator[bytes]:
        """Stream an object's bytes; raises ObjectNotFoundError if it does not exist."""

    @abstractmethod
    async def size(self, key: str) -> int:
        """Size in bytes; raises ObjectNotFoundError if the object does not exist."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete an object; returns False if it did not exist."""

    @abstractmethod
    async def exists(self, key: str) -> bool: ...
