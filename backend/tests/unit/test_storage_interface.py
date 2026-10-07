import pytest

from app.storage.base import ObjectNotFoundError, StorageError, StorageProvider


def test_storage_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        StorageProvider()  # type: ignore[abstract]


def test_not_found_is_a_storage_error() -> None:
    assert issubclass(ObjectNotFoundError, StorageError)
