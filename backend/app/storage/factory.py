from app.config import Settings
from app.storage.base import StorageProvider
from app.storage.local import LocalStorageProvider


def create_storage(settings: Settings) -> StorageProvider:
    # Only the local backend exists today; an S3-compatible backend plugs in here.
    return LocalStorageProvider(settings.storage_local_path)
