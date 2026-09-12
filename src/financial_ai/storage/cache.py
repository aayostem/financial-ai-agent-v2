from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError

from financial_ai.config import get_settings
from financial_ai.utils.exceptions import CacheConnectionError, CacheOperationError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Namespace constants
NS_CHUNKS = "chunks"
NS_EMBEDDINGS = "embeddings"
NS_QUERY = "query"
NS_ANALYSIS = "analysis"
NS_MARKET = "market"
NS_HEALTH = "health"


def build_key(*parts: str) -> str:
    return "finai:" + ":".join(parts)


class CacheClient:
    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._redis: Redis | None = None

    async def connect(self) -> None:
        if self._redis is not None:
            logger.warning("CacheClient.client() called on already-connected client")
            return

        settings = get_settings()

        try:
            self._pool = ConnectionPool.from_url(
                settings.REDIS_URL.get_secret_value(),
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
                decode_responses=True,
                health_check_interval=30,
            )
            self._redis = Redis(connection_pool=self._pool)

            await self._redis.ping()
            logger.info(
                "Redis connection pool established - host=%s port=%d db=%d",
                settings.REDIS_HOST,
                settings.REDIS_PORT,
                settings.REDIS_DB,
            )
        except RedisError as exc:
            logger.error("Failed to connect to Redis: %s", exc)
            raise CacheConnectionError(
                f"Cannot connect to Redis at {settings.REDIS_HOST}: {settings.REDIS_PORT} - {exc}"
            ) from exc

    async def disconnect(self) -> None:
        if self._redis is None:
            return
        await self._redis.aclose()
        if self._pool is not None:
            await self._pool.aclose()
        self._redis = None
        self._pool = None
        logger.info("Redis connection pool closed")

    async def get(self, key: str) -> Any | None:
        self._assert_connected()
        assert self._redis is not None
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Cache value for key '%s' is not valid JSON: %s", key, exc)
            return None
        except RedisError as exc:
            logger.error("Cache GET failed for key '%s':%s", key, exc)
            raise CacheOperationError(f"GET {key} failed: {exc}") from exc

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        self._assert_connected()
        assert self._redis is not None
        settings = get_settings()
        effective_ttl = ttl if ttl is not None else settings.REDIS_DEFAULT_TTL_SECONDS

        try:
            serialized = json.dumps(value, default=str)
            await self._redis.setex(name=key, time=effective_ttl, value=serialized)
            logger.debug("Cache SET key='%s' ttl=%ds", key, effective_ttl)
            return True
        except (TypeError, ValueError) as exc:
            logger.error("Cannot serialize value for key '%s': %s", key, exc)
            raise CacheOperationError(
                f"Value for key '{key}' i not JSON-serializable: {exc}"
            ) from exc
        except RedisError as exc:
            logger.error("Cache SET failed for key '%s': %s", key, exc)
            raise CacheOperationError(f"SET {key} failed: {exc}") from exc

    async def delete(self, key: str) -> bool:
        self._assert_connected()
        assert self._redis is not None
        try:
            deleted = await self._redis.delete(key)
            return bool(deleted)
        except RedisError as exc:
            logger.error("Cache DELETE failed for key '%s': %s", key, exc)
            raise CacheOperationError(f"DELETE {key} failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        self._assert_connected()
        assert self._redis is not None
        try:
            return bool(await self._redis.exists(key))
        except RedisError as exc:
            raise CacheOperationError(f"EXISTS {key} failed: {exc}") from exc

    async def expire(self, key: str, ttl: int) -> bool:
        self._assert_connected()
        assert self._redis is not None
        try:
            return bool(await self._redis.expire(key, ttl))
        except RedisError as exc:
            raise CacheOperationError(f"EXPIRE {key} failed: {exc}") from exc

    async def clear_namespace(self, namespace: str) -> int:
        self._assert_connected()
        assert self._redis is not None
        pattern = f"finai:{namespace}:*"
        deleted = 0
        try:
            async for key in self._redis.scan_iter(pattern):
                await self._redis.delete(key)
                deleted += 1
            logger.info("Cleared %d keys matching pattern '%s'", deleted, pattern)
            return deleted
        except RedisError as exc:
            raise CacheOperationError(f"clear_namespace '{namespace}' failed: {exc}") from exc

    # HEALTH
    async def health_check(self) -> dict[str, Any]:
        if self._redis is None:
            return {"status": "disconnected", "error": "Client not initialized"}

        try:
            await self._redis.ping()
            info = await self._redis.info("server")
            pool_stats = self._pool_stats()

            return {
                "status": "healthy",
                "redis_version": info.get("redis_version"),
                "used_memory_human": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
                **pool_stats,
            }
        except RedisError as exc:
            logger.error("Redis health check failed: %s", exc)
            raise CacheConnectionError(f"Health check failed: {exc}") from exc

    # INTERNAL
    def _assert_connected(self) -> None:
        if self._redis is None:
            raise CacheConnectionError("CacheClient is not connected. Call connect() first.")

    def _pool_stats(self) -> dict[str, Any]:
        if self._pool is None:
            return {}
        stats: dict[str, Any] = {"pool_max_connections": self._pool.max_connections}
        created = getattr(self._pool, "_created_connections", None)
        if created is not None:
            stats["pool_created_connections"] = created
        return stats


# Singleton accessor
_cache_client: CacheClient | None = None


async def get_cache_client() -> CacheClient:
    global _cache_client
    if _cache_client is None:
        _cache_client = CacheClient()
    return _cache_client
