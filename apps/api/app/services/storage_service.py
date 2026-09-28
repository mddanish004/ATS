import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import settings


class PrivateStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def delete(self, key: str) -> None: ...


class StorageError(Exception):
    pass


class LocalPrivateStorage:
    """Private local storage boundary, replaceable by an R2 implementation."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise StorageError("Private storage write failed") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Private storage cleanup failed") from exc


def get_private_storage() -> PrivateStorage:
    return LocalPrivateStorage(settings.private_upload_dir)
