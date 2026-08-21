from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    APP_ENV: str = Field(default="production")
    DEBUG: bool = Field(default=False)
    # SECRET_KEY: str
    METRICS_ENABLED: bool = Field(default=True)
    METRICS_HTTP_SERVER_ENABLED: bool = Field(default=False)
    METRICS_PORT: int = Field(default=8000)

    # PostgreSQL
    # POSTGRES_USER: str
    # POSTGRES_PASSWORD: str
    # POSTGRES_DB: str

    DATABASE_URL: Optional[str] = None
    ALEMBIC_DATABASE_URL: Optional[str] = None

    # Redis
    # REDIS_PASSWORD: str
    REDIS_URL: Optional[str] = None

    # JWT
    # JWT_SECRET_KEY: str
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

    # Qdrant
    QDRANT_URL: str = Field(default="http://localhost:6338")
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_PREFER_GRPC: bool = Field(default=False)
    QDRANT_TIMEOUT_S: float = Field(default=5.0)

    MLFLOW_TRACKING_URI: str = Field(default="https://mlflow.ghoniem.online")
    EXPERIMENT_NAME: str = Field(default="sera-ai")
    MLFLOW_ASYNC_LOGGING: bool = Field(default=True)

    # ─── Agent: providers ───
    # Codex and Antigravity both speak the OpenAI wire format, so they share
    # one adapter and differ only by base_url. Ollama gets its own adapter
    # because its performance knobs (keep_alive, num_ctx) have no OpenAI analogue.
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434")
    OLLAMA_DEFAULT_MODEL: str = Field(default="qwen3:4b")
    OLLAMA_KEEP_ALIVE: str = Field(default="30m")  # never let the model leave VRAM
    OLLAMA_NUM_CTX: int = Field(default=8192)
    OLLAMA_NUM_PREDICT: int = Field(default=1024)

    CODEX_BASE_URL: Optional[str] = None
    CODEX_DEFAULT_MODEL: str = Field(default="gpt-5-codex")

    ANTIGRAVITY_BASE_URL: Optional[str] = None
    ANTIGRAVITY_DEFAULT_MODEL: str = Field(default="gemini-3-pro")

    DEFAULT_PROVIDER: str = Field(default="ollama")
    PROVIDER_TIMEOUT_S: float = Field(default=120.0)
    PROVIDER_MAX_KEEPALIVE: int = Field(default=32)
    PROVIDER_HEALTH_INTERVAL_S: int = Field(default=30)

    # ─── Agent: latency budget (see docs/agent/01-latency-contract.md) ───
    AGENT_OVERHEAD_BUDGET_MS: int = Field(default=300)   # p95 target, excl. LLM
    AGENT_REQUEST_DEADLINE_S: float = Field(default=180.0)
    AGENT_MAX_MODEL_CALLS: int = Field(default=8)
    AGENT_MAX_TOOL_CALLS: int = Field(default=16)

    # ─── Agent: cache TTLs (seconds; 0 disables that layer) ───
    CACHE_TTL_EMBEDDING: int = Field(default=86_400)
    CACHE_TTL_RETRIEVAL: int = Field(default=300)
    CACHE_TTL_ANSWER: int = Field(default=0)   # off until Phase 7 — see decision #5
    CACHE_KEY_PREFIX: str = Field(default="sera")

    # ─── Agent: inference thread pools ───
    # Bounded so concurrent torch forward passes cannot OOM the GPU.
    EMBED_MAX_CONCURRENCY: int = Field(default=2)
    RERANK_MAX_CONCURRENCY: int = Field(default=2)
    EMBED_BATCH_WINDOW_MS: int = Field(default=0)  # 0 = no micro-batching yet

    # ─── Agent: cold lane ───
    COLD_LANE_WORKERS: int = Field(default=4)
    COLD_LANE_MAXSIZE: int = Field(default=10_000)
    COLD_LANE_DRAIN_TIMEOUT_S: float = Field(default=5.0)

    # ─── RAG hyperparameters (tunable; defaults live in code) ───
    # Override via env if needed; otherwise change these values in the file
    # and commit — they're tracked in git and easy to diff over time.
    COLLECTION_NAME: str = Field(default="medical_o1_sft")
    TOTAL_ROWS: int = Field(default=19_704)
    EVAL_SIZE: int = Field(default=300)       # held out — never in Qdrant
    RAGAS_SAMPLES: int = Field(default=50)    # costly LLM eval subset
    TOP_K: int = Field(default=20)            # retrieve from Qdrant
    TOP_N: int = Field(default=5)             # keep after reranking → sent to LLM
    RANDOM_STATE: int = Field(default=42)

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
