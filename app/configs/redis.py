"""Async Redis client.

Phase 0 fix: the previous version read ``settings.REDIS_HOST`` / ``REDIS_PORT`` /
``REDIS_PASSWORD``, none of which exist in ``config.py`` — the module could not be
imported. It now builds from ``settings.REDIS_URL`` (credentials belong in the URL).

The ``asyncio.Lock`` is also created inside ``__init__`` rather than at class-definition
time. A module-level lock binds to whichever event loop imports the module first, which
breaks under pytest-asyncio and under any multi-loop deployment.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as redis

from app.configs.config import settings
from app.configs.logger import get_logger

logger = get_logger()


class RedisClient:
    """Singleton async Redis wrapper with an explicit lifecycle."""

    _instance: "RedisClient | None" = None
    _instance_lock: asyncio.Lock | None = None

    def __init__(self) -> None:
        self.client: redis.Redis | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> "RedisClient":
        if cls._instance_lock is None:
            cls._instance_lock = asyncio.Lock()

        if cls._instance is None:
            async with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def connect(self) -> redis.Redis | None:
        """Connect and verify with a PING. Returns None when Redis is not configured.

        Redis is a cache, not a system of record: a missing REDIS_URL degrades the
        agent to uncached operation rather than failing startup.
        """
        if self.client is not None:
            return self.client

        if not settings.REDIS_URL:
            logger.warning("REDIS_URL not set — caching disabled, agent will run uncached")
            return None

        async with self._lock:
            if self.client is not None:
                return self.client

            try:
                client = redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=False,  # embeddings are stored as raw float32 bytes
                    health_check_interval=30,
                    socket_connect_timeout=2.0,
                    socket_timeout=2.0,
                    retry_on_timeout=True,
                )
                await client.ping()
            except Exception as exc:
                logger.error("❌ Redis connection failed: %s", exc)
                raise

            self.client = client
            logger.info("🚀 Redis connected successfully")
            return self.client

    async def get(self) -> redis.Redis | None:
        if self.client is None:
            await self.connect()
        return self.client

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
            logger.info("🧹 Redis connection closed")


redis_client = RedisClient()
