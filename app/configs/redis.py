import asyncio
import redis.asyncio as redis
from app.configs.logger import get_logger
from app.configs.config import settings

logger = get_logger()


class RedisClient:
    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.client: redis.Redis | None = None

    # =========================
    # 🚀 Singleton getter
    # =========================
    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # =========================
    # 🚀 CONNECT (explicit lifecycle)
    # =========================
    async def connect(self):
        if self.client:
            return self.client

        try:
            self.client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                decode_responses=True,
                health_check_interval=30,
                password=settings.REDIS_PASSWORD
            )

            await self.client.ping()

            logger.info("🚀 Redis connected successfully")

        except Exception as e:
            logger.error("❌ Redis connection failed: %s", str(e))
            raise

        return self.client

    # =========================
    # 📦 GET CLIENT
    # =========================
    async def get(self):
        if not self.client:
            await self.connect()
        return self.client

    # =========================
    # 🧹 CLOSE CONNECTION
    # =========================
    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("🧹 Redis connection closed")


# =========================
# Public singleton
# =========================
redis_client = RedisClient()