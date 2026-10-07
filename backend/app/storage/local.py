import asyncio
import contextlib
import hashlib
import os
import re
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
import aiofiles.os

from app.storage.base import (
    DEFAULT_CHUNK_SIZE,
    InvalidStorageKeyError,
    ObjectExistsError,
    ObjectNotFoundError,
    StorageError,
    StorageProvider,
    StoredObject,
)

_KEY_PATTERN = re.compile(r"[a-z]{1,32}/[0-9a-f]{2}/[0-9a-f]{32}")
_INCOMING_DIR = ".incoming"


class LocalStorageProvider(StorageProvider):
    """Stores objects as files under a private root directory (development backend)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, mode=0o700, exist_ok=True)
        (self._root / _INCOMING_DIR).mkdir(mode=0o700, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise InvalidStorageKeyError("storage key is not a generated identifier")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise InvalidStorageKeyError("storage key escapes the storage root")
        return path

    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> StoredObject:
        final = self._path(key)
        incoming = self._root / _INCOMING_DIR / secrets.token_hex(16)
        digest = hashlib.sha256()
        size = 0
        try:
            async with aiofiles.open(incoming, "xb") as handle:
                async for chunk in chunks:
                    await handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            await asyncio.to_thread(os.chmod, incoming, 0o600)
            await aiofiles.os.makedirs(final.parent, mode=0o700, exist_ok=True)
            try:
                await aiofiles.os.link(incoming, final)  # fails instead of overwriting
            except FileExistsError:
                raise ObjectExistsError("an object with this key already exists") from None
        except (OSError, StorageError) as error:
            if isinstance(error, StorageError):
                raise
            raise StorageError("could not write object") from error
        finally:
            with contextlib.suppress(OSError):
                await aiofiles.os.remove(incoming)
        return StoredObject(key=key, size_bytes=size, sha256=digest.hexdigest())

    async def open(self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> AsyncIterator[bytes]:
        path = self._path(key)
        try:
            handle = await aiofiles.open(path, "rb")
        except FileNotFoundError:
            raise ObjectNotFoundError("object does not exist") from None
        except OSError as error:
            raise StorageError("could not read object") from error
        try:
            while chunk := await handle.read(chunk_size):
                yield chunk
        finally:
            await handle.close()

    async def size(self, key: str) -> int:
        try:
            return (await aiofiles.os.stat(self._path(key))).st_size
        except FileNotFoundError:
            raise ObjectNotFoundError("object does not exist") from None
        except OSError as error:
            raise StorageError("could not stat object") from error

    async def delete(self, key: str) -> bool:
        try:
            await aiofiles.os.remove(self._path(key))
        except FileNotFoundError:
            return False
        except OSError as error:
            raise StorageError("could not delete object") from error
        return True

    async def exists(self, key: str) -> bool:
        return await aiofiles.os.path.isfile(self._path(key))
