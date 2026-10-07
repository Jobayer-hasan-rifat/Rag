from abc import ABC, abstractmethod


class StorageError(Exception):
    """Raised when a storage backend fails."""


class ObjectNotFoundError(StorageError):
    """Raised when a requested object does not exist."""


class StorageProvider(ABC):
    """Backend-agnostic object storage (local filesystem, S3, S3-compatible).

    Keys are opaque, server-generated identifiers; implementations must never
    derive filesystem paths from user-supplied file names.
    """

    @abstractmethod
    async def save(self, key: str, data: bytes, *, content_type: str | None = None) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...
