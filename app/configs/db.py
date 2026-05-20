from typing import AsyncGenerator

from sqlmodel import SQLModel
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from fastapi import Request
from app.configs.logger import get_logger

logger = get_logger()


class Database:
    """
    🚀 Singleton Async Database Manager
    """

    _instance = None

    def __init__(self):
        self.engine = None
        self.session_factory = None

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        """
        asyncpg caches prepared statements per pooled connection.
        After out-of-band schema changes, those cached plans can go stale and
        raise InvalidCachedStatementError on otherwise normal queries.
        """
        url = make_url(database_url)

        if url.drivername != "postgresql+asyncpg":
            return database_url

        if "prepared_statement_cache_size" in url.query:
            return database_url

        return url.update_query_dict(
            {"prepared_statement_cache_size": "0"}
        ).render_as_string(hide_password=False)

    # =========================
    # 🚀 Singleton
    # =========================
    @classmethod
    def get_instance(cls) -> "Database":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # =========================
    # 🚀 CONNECT
    # =========================
    async def connect(self, database_url: str):
        if self.engine:
            return

        try:
            normalized_database_url = self._normalize_database_url(database_url)

            if normalized_database_url != database_url:
                logger.info("🧩 asyncpg prepared statement cache disabled for pooled connections")

            self.engine = create_async_engine(
                normalized_database_url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
            )

            self.session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            # Force a real DB round-trip before reporting success.
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            logger.info("🚀 PostgreSQL connected successfully")

        except Exception as e:
            if self.engine:
                await self.engine.dispose()
            self.engine = None
            self.session_factory = None
            logger.error("❌ DB connection failed: %s", str(e))
            raise

    # =========================
    # 📦 SESSION (internal use)
    # =========================
    def session(self) -> AsyncSession:
        if not self.session_factory:
            raise Exception("❌ Database not initialized")

        return self.session_factory()

    # =========================
    # 📦 FASTAPI DEPENDENCY
    # =========================
    async def get_db(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.session() as session:
            try:
                yield session
            finally:
                await session.close()


    # =========================
    # 🚀 CREATE TABLES (DEV ONLY)
    # =========================
    async def create_tables(self):
        if not self.engine:
            raise Exception("❌ Engine not initialized")

        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        logger.info("📦 Database tables created")

    # =========================
    # 🧹 CLOSE
    # =========================
    async def close(self):
        if self.engine:
            await self.engine.dispose()
            logger.info("🧹 PostgreSQL connection closed")


# =========================
# 🚀 GLOBAL INSTANCE
# =========================
db = Database.get_instance()

