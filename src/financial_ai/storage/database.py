from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from financial_ai.config import get_settings
from financial_ai.utils.exceptions import DatabaseConnectionError, DatabaseQueryError

logger = logging.getLogger(__name__)

# ORM BASE


class Base(DeclarativeBase):
    pass


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL.get_secret_value(),
        # pool
        pool_size=settings.DB_POOL_MIN_SIZE,
        max_overflow=settings.DB_POOL_MAX_SIZE - settings.DB_POOL_MIN_SIZE,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        pool_pre_ping=True,
        pool_timeout=settings.DB_CONNECT_SECONDS,
        # async-specific
        connect_args={
            "command_timeout": settings.DB_QUERY_TIMEOUT_SECONDS,
            "server_settings": {"application_name": settings.APP_NAME},
        },
        echo=settings.DEBUG,
        echo_pool=settings.DEBUG,
    )
    logger.info(
        "Database engine created - host=%s db=%s pool_size=%d max_overflow=%d",
        settings.POSTGRES_HOST,
        settings.POSTGRES_DB,
        settings.DB_POOL_MIN_SIZE,
        settings.DB_POOL_MAX_SIZE - settings.DB_POOL_MIN_SIZE,
    )
    return engine


class DatabaseClient:
    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    # Lifecycle

    async def connect(self) -> None:
        if self._engine is not None:
            logger.warning("DatabaseClient.connect() called on already-connected client")
            return

        try:
            self._engine = _build_engine()
            self._session_factory = async_sessionmaker(
                bind=self._engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autobegin=True,
                autoflush=False,
            )
            await self._verify_connection()
            logger.info("Database connection pool established")

        except Exception as exc:
            logger.error("Failed to establish database connection: %s", exc)
            raise DatabaseConnectionError(
                f"Cannot connect to PostgreSQL at"
                f"{get_settings().POSTGRES_HOST}:{get_settings().POSTGRES_PORT}- {exc}"
            ) from exc

    async def disconnect(self) -> None:
        if self._engine is None:
            return
        await self._engine.dispose()
        self._engine = None
        self._session_factory = None
        logger.info("Database connection pool closed")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._session_factory is None:
            raise DatabaseConnectionError("DatabaseClient is not connected. Call connect() first.")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.error("Session rolled back due to: %s", exc)
                raise DatabaseQueryError(str(exc)) from exc
            finally:
                await session.close()

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[AsyncConnection, None]:
        if self._engine is None:
            raise DatabaseConnectionError("DatabaseClient is not connected. call connect() first")
        async with self._engine.begin() as conn:
            yield conn

    # HEALTH AND DIAGNOSTIC
    async def health_check(self) -> dict[str, Any]:
        if self._engine is None:
            return {"status": "disconnected", "error": "client not initialized"}

        try:
            async with self._engine.connect() as conn:
                row = await conn.execute(text("SELECT version(), pg_postmaster_start_time()"))
                version, start_time = row.one()

                pool = self._engine.pool
                return {
                    "status": "healthy",
                    "postgres_version": version.split(" ")[1],
                    "server_start_time": str(start_time),
                    "pool_size": pool.size(),
                    "pool_checked_out": pool.checkedout(),
                    "pool_overflow": pool.overflow(),
                }
        except Exception as exc:
            logger.error("Database health check failed: %s", exc)
            raise DatabaseConnectionError(f"Health check failed: {exc}") from exc

    async def verify_pgvector(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                )
                installed = result.scalar is not None
                if installed:
                    logger.info("pgvector extension verified")
                else:
                    logger.error(
                        "pgvector extension Not found. Run: CREATE EXTENSION IF NOT EXISTS VECTOR;"
                    )
                return installed
        except Exception as exc:
            logger.error("Failed to verify pgvector: %s", exc)
            return False

    # INTERNAL
    async def _verify_connection(self) -> None:
        assert self._engine is not None
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise DatabaseConnectionError(f"Database connectivity check failed: {exc}") from exc

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise DatabaseConnectionError("DatabaseClient is not connected.")
        return self._engine


_db_client: DatabaseClient | None = None


async def get_db_client() -> DatabaseClient:
    global _db_client
    if _db_client is None:
        _db_client = DatabaseClient()
    return _db_client


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    client = await get_db_client()
    async with client.session() as session:
        yield session
