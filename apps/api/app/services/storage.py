"""Raw source storage.

An abstraction over where uploaded source files live. Dev uses the local
filesystem under ``STORAGE_DIR``; production swaps in object storage behind the
same ``ContentStorage`` protocol without touching callers.
"""

from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class StorageError(Exception):
    pass


class ContentStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


def document_key(document_id: str) -> str:
    return f"documents/{document_id}"


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        # Keys are app-generated ("documents/<uuid>"); still guard traversal.
        resolved = (self._root / key).resolve()
        root = self._root.resolve()
        if root not in resolved.parents and resolved != root:
            raise StorageError(f"key escapes storage root: {key!r}")
        return resolved

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"no stored content for key {key!r}") from exc

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage() -> ContentStorage:
    return LocalFileStorage(get_settings().storage_dir)
