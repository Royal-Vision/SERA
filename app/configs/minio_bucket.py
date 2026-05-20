import json
from typing import Optional

from minio import Minio
from minio.error import S3Error

from app.configs.config import settings


class MinioClient:
    _instance: Optional["MinioClient"] = None
    _client: Optional[Minio] = None

    def __new__(cls) -> "MinioClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._client is None:
            self._client = Minio(
                endpoint=settings.MINIO_S3_ENDPOINT_URL,
                access_key=settings.MINIO_ACCESS_KEY_ID,
                secret_key=settings.MINIO_SECRET_ACCESS_KEY,
                secure=True,
            )

    @property
    def client(self) -> Minio:
        if self._client is None:
            raise RuntimeError("MinIO client not initialized")
        return self._client

    def ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(settings.MINIO_BUCKET):
                self.client.make_bucket(settings.MINIO_BUCKET)

            self.client.set_bucket_policy(
                settings.MINIO_BUCKET,
                json.dumps(self._public_read_policy()),
            )

        except S3Error as exc:
            raise RuntimeError(f"MinIO initialization failed: {exc}") from exc

    def upload_file(
        self,
        file_data,
        object_name: str,
        content_type: str,
    ) -> str:
        self.client.put_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=object_name,
            data=file_data,
            length=-1,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )

        return self.build_public_url(object_name)

    def delete_file(self, object_name: str) -> None:
        self.client.remove_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=object_name,
        )

    def build_public_url(self, object_name: str) -> str:
        return (
            f"{settings.MINIO_PUBLIC_URL.rstrip('/')}/"
            f"{settings.MINIO_BUCKET}/"
            f"{object_name}"
        )

    @staticmethod
    def _public_read_policy() -> dict:
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [
                        f"arn:aws:s3:::{settings.MINIO_BUCKET}/*"
                    ],
                }
            ],
        }


minio_client = MinioClient()