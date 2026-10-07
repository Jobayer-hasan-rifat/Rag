import hashlib
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.storage.base import (
    InvalidStorageKeyError,
    ObjectExistsError,
    ObjectNotFoundError,
    StorageProvider,
    generate_storage_key,
)
from app.storage.local import LocalStorageProvider


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _failing_chunks() -> AsyncIterator[bytes]:
    yield b"partial"
    raise RuntimeError("client went away")


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorageProvider:
    return LocalStorageProvider(tmp_path / "root")


async def _read_all(storage: StorageProvider, key: str, chunk_size: int = 1024) -> list[bytes]:
    return [chunk async for chunk in storage.open(key, chunk_size=chunk_size)]


def test_generated_keys_are_opaque_unique_and_well_formed() -> None:
    keys = {generate_storage_key() for _ in range(1000)}

    assert len(keys) == 1000
    for key in list(keys)[:20]:
        prefix, shard, token = key.split("/")
        assert prefix == "documents" and len(shard) == 2 and len(token) == 32
        assert token.startswith(shard)


async def test_save_returns_size_and_checksum_and_persists(storage: LocalStorageProvider) -> None:
    key = generate_storage_key()

    stored = await storage.save(key, _chunks(b"hello ", b"world"))

    assert stored.size_bytes == 11
    assert stored.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert await storage.exists(key)
    assert await storage.size(key) == 11
    assert b"".join(await _read_all(storage, key)) == b"hello world"


async def test_open_streams_in_bounded_chunks(storage: LocalStorageProvider) -> None:
    key = generate_storage_key()
    await storage.save(key, _chunks(b"x" * 10_000))

    chunks = await _read_all(storage, key, chunk_size=4096)

    assert [len(chunk) for chunk in chunks] == [4096, 4096, 1808]


async def test_saving_over_an_existing_key_is_refused_and_keeps_the_original(
    storage: LocalStorageProvider,
) -> None:
    key = generate_storage_key()
    await storage.save(key, _chunks(b"original"))

    with pytest.raises(ObjectExistsError):
        await storage.save(key, _chunks(b"attacker"))

    assert b"".join(await _read_all(storage, key)) == b"original"


async def test_failed_upload_leaves_no_object_and_no_temporary_file(
    storage: LocalStorageProvider, tmp_path: Path
) -> None:
    key = generate_storage_key()

    with pytest.raises(RuntimeError):
        await storage.save(key, _failing_chunks())

    assert not await storage.exists(key)
    assert list((tmp_path / "root" / ".incoming").iterdir()) == []


async def test_missing_objects_report_not_found(storage: LocalStorageProvider) -> None:
    key = generate_storage_key()

    assert not await storage.exists(key)
    with pytest.raises(ObjectNotFoundError):
        await storage.size(key)
    with pytest.raises(ObjectNotFoundError):
        await _read_all(storage, key)


async def test_delete_is_idempotent(storage: LocalStorageProvider) -> None:
    key = generate_storage_key()
    await storage.save(key, _chunks(b"data"))

    assert await storage.delete(key) is True
    assert await storage.delete(key) is False
    assert not await storage.exists(key)


@pytest.mark.parametrize(
    "key",
    [
        "../outside",
        "documents/../../etc/passwd",
        "/etc/passwd",
        "documents/ab/" + "a" * 31,
        "documents/ab/" + "A" * 32,
        "documents/zz/" + "g" * 32,
        "documents\\ab\\" + "a" * 32,
        "documents/ab/" + "a" * 32 + "/extra",
        "documents/ab/" + "a" * 32 + "\x00",
        "",
        "C:/Windows/win.ini",
        "user-supplied-file-name.pdf",
    ],
)
async def test_only_generated_keys_are_accepted(storage: LocalStorageProvider, key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        await storage.save(key, _chunks(b"x"))
    with pytest.raises(InvalidStorageKeyError):
        await storage.exists(key)
    with pytest.raises(InvalidStorageKeyError):
        await storage.delete(key)


async def test_objects_never_land_outside_the_storage_root(
    storage: LocalStorageProvider, tmp_path: Path
) -> None:
    for _ in range(5):
        await storage.save(generate_storage_key(), _chunks(b"data"))

    created = [p for p in tmp_path.rglob("*") if p.is_file()]  # noqa: ASYNC240

    assert created and all((tmp_path / "root") in p.parents for p in created)


def test_storage_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        StorageProvider()  # type: ignore[abstract]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
async def test_stored_objects_and_directories_are_owner_only(storage: LocalStorageProvider) -> None:
    key = generate_storage_key()
    await storage.save(key, _chunks(b"data"))

    path = storage._path(key)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
