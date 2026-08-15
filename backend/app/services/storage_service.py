"""Storage backend selection."""

from functools import lru_cache

from app.core.config import settings
from app.core.exceptions import AppError
from app.storage.base import StorageBackend
from app.storage.local import LocalStorageBackend


@lru_cache
def get_storage() -> StorageBackend:
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorageBackend(settings.storage_local_root, settings.storage_tmp_dir)
    raise AppError(
        f"Unknown storage backend: {settings.storage_backend}",
        code="INVALID_STORAGE_BACKEND",
        status_code=500,
    )


def reset_storage_cache() -> None:
    """Test hook - drops the cached backend after settings are overridden."""
    get_storage.cache_clear()
