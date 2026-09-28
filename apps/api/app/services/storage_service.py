import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import settings


class PrivateStorage(Protocol):
    def upload_object(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...

    def download_object(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def generate_signed_url(self, key: str, *, expires_in: int) -> str: ...


class StorageError(Exception):
    pass


class StorageConfigurationError(StorageError):
    pass


class LocalPrivateStorage:
    """Private local storage boundary used for development and tests."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("Invalid storage key")
        return path

    def upload_object(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
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

    def download_object(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError("Private storage read failed") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Private storage cleanup failed") from exc

    def generate_signed_url(self, key: str, *, expires_in: int) -> str:
        self._path(key)
        expires_at = int(time.time()) + expires_in
        return f"private://local/{key}?expires={expires_at}"


class R2PrivateStorage:
    """Cloudflare R2 storage via the S3-compatible API."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket_name: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - dependency/config guard
            raise StorageConfigurationError(
                "R2 storage dependencies are unavailable"
            ) from exc

        self.bucket_name = bucket_name
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
            config=Config(signature_version="s3v4"),
        )

    def upload_object(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        try:
            self._client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=dict(metadata or {}),
            )
        except Exception as exc:
            raise StorageError("Private storage write failed") from exc

    def download_object(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except Exception as exc:
            raise StorageError("Private storage read failed") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket_name, Key=key)
        except Exception as exc:
            raise StorageError("Private storage cleanup failed") from exc

    def generate_signed_url(self, key: str, *, expires_in: int) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise StorageError("Private storage signing failed") from exc


def _configured_r2_storage() -> R2PrivateStorage:
    missing = [
        name
        for name, value in (
            ("R2_ENDPOINT_URL", settings.r2_endpoint_url),
            ("R2_BUCKET_NAME", settings.r2_bucket_name),
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
        )
        if not value
    ]
    if missing:
        raise StorageConfigurationError("R2 storage is not fully configured")
    return R2PrivateStorage(
        endpoint_url=settings.r2_endpoint_url or "",
        bucket_name=settings.r2_bucket_name or "",
        access_key_id=settings.r2_access_key_id or "",
        secret_access_key=settings.r2_secret_access_key or "",
        region_name=settings.r2_region_name,
    )


def get_private_storage() -> PrivateStorage:
    if settings.resume_storage_backend == "r2":
        return _configured_r2_storage()
    if settings.resume_storage_backend == "local":
        return LocalPrivateStorage(settings.private_upload_dir)
    raise StorageConfigurationError("Unsupported resume storage backend")
