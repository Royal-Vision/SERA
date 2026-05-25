from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    APP_ENV: str = Field(default="production")
    DEBUG: bool = Field(default=False)
    SECRET_KEY: str
    METRICS_ENABLED: bool = Field(default=True)
    METRICS_HTTP_SERVER_ENABLED: bool = Field(default=False)
    METRICS_PORT: int = Field(default=8000)

    # PostgreSQL
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str

    DATABASE_URL: Optional[str] = None
    ALEMBIC_DATABASE_URL: Optional[str] = None

    # Redis
    REDIS_PASSWORD: str
    REDIS_URL: Optional[str] = None

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # CORS
    ALLOWED_ORIGINS: str = ""

    # OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None

    FACEBOOK_CLIENT_ID: Optional[str] = None
    FACEBOOK_CLIENT_SECRET: Optional[str] = None
    FACEBOOK_REDIRECT_URI: Optional[str] = None

    # AI Providers
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None

    # Email
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None

    # S3 / MinIO
    S3_ENDPOINT: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_BUCKET: Optional[str] = None
    S3_SECURE: bool = True

    QDRANT_API_KEY: str

    MLFLOW_TRACKING_URI: str
    EXPERIMENT_NAME: str

    COLLECTION_NAME: str
    TOTAL_ROWS: int
    EVAL_SIZE: int      # held out — never in Qdrant
    RAGAS_SAMPLES: int       # costly LLM eval subset
    TOP_K: int       # retrieve from Qdrant
    TOP_N: int        # keep after reranking → sent to LLM
    RANDOM_STATE: int

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        if not self.ALLOWED_ORIGINS:
            return []

        return [
            origin.strip()
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
