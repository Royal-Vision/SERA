"""MinIO / S3 object storage client.

Phase 0 fixes:

1. The previous version read ``settings.MINIO_*``; ``config.py`` defines ``S3_*``.
   The module could not be imported.
2. ``minio_client = MinioClient()`` ran at module level, so a bad or absent config
   crashed on *import* rather than at first use. The client is now lazy.
3. **The bucket policy was public-read.** For a medical document store that makes every
   uploaded PDF world-readable to anyone who can guess an object name. Replaced with
   short-lived presigned URLs; the bucket stays private.

The MinIO SDK is synchronous. Callers on the request path must dispatch through
``anyio.to_thread.run_sync`` — see ``app/agent/runtime/threadpool.py``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import BinaryIO, Optional

from minio import Minio
from minio.error import S3Error

from app.configs.config import settings
from app.configs.logger import get_logger

logger = get_logger()

DEFAULT_PRESIGN_TTL = timedelta(minutes=15)


class MinioNotConfiguredError(RuntimeError):
    """Raised when object storage is used without S3_* settings present."""


class MinioClient:
    """Singleton MinIO wrapper. The underlying client is built on first use."""

    _instance: Optional["MinioClient"] = None

    def __new__(cls) -> "MinioClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
        return cls._instance

    @property
    def configured(self) -> bool:
        return bool(
            settings.S3_ENDPOINT
            and settings.S3_ACCESS_KEY
            and settings.S3_SECRET_KEY
            and settings.S3_BUCKET
        )

    @property
    def client(self) -> Minio:
        if self._client is None:
            if not self.configured:
                raise MinioNotConfiguredError(
                    "Object storage unavailable: set S3_ENDPOINT, S3_ACCESS_KEY, "
                    "S3_SECRET_KEY and S3_BUCKET."
                )
            self._client = Minio(
                endpoint=settings.S3_ENDPOINT,
                access_key=settings.S3_ACCESS_KEY,
                secret_key=settings.S3_SECRET_KEY,
                secure=settings.S3_SECURE,
            )
            logger.info("🚀 MinIO client initialized | endpoint=%s", settings.S3_ENDPOINT)
        return self._client

    def ensure_bucket(self) -> bool:
        """Create the bucket if absent. Returns False when storage is not configured.

        Deliberately does NOT set a bucket policy — the bucket stays private and
        access is granted per-object through presigned URLs.
        """
        if not self.configured:
            logger.warning("S3_* not set — object storage disabled")
            return False

        try:
            if not self.client.bucket_exists(settings.S3_BUCKET):
                self.client.make_bucket(settings.S3_BUCKET)
                logger.info("📦 Created private bucket '%s'", settings.S3_BUCKET)
            return True
        except S3Error as exc:
            raise RuntimeError(f"MinIO initialization failed: {exc}") from exc

    def upload_file(
        self,
        file_data: BinaryIO,
        object_name: str,
        content_type: str,
        length: int = -1,
    ) -> str:
        self.client.put_object(
            bucket_name=settings.S3_BUCKET,
            object_name=object_name,
            data=file_data,
            length=length,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )
        return object_name

    def delete_file(self, object_name: str) -> None:
        self.client.remove_object(
            bucket_name=settings.S3_BUCKET,
            object_name=object_name,
        )

    def presigned_url(
        self,
        object_name: str,
        expires: timedelta = DEFAULT_PRESIGN_TTL,
    ) -> str:
        """Time-limited read URL. Never log the result — it is a bearer credential."""
        return self.client.presigned_get_object(
            bucket_name=settings.S3_BUCKET,
            object_name=object_name,
            expires=expires,
        )


def get_minio_client() -> MinioClient:
    """Accessor used by lifespan and services. Import this, not a module-level instance."""
    return MinioClient()
