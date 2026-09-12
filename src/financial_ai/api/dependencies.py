from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends

from financial_ai.retrieval.query_engine import QueryEngine
from financial_ai.storage.cache import CacheClient, get_cache_client
from financial_ai.storage.database import DatabaseClient, get_db_client
from financial_ai.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)

_query_engine: QueryEngine | None = None
_vector_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store


def get_query_engine() -> QueryEngine:
    global _query_engine
    if _query_engine is None:
        vs = get_vector_store()
        _query_engine = QueryEngine(vector_store=vs)
    return _query_engine


# FastAPI Dependency Function


async def db_client() -> DatabaseClient:
    return await get_db_client()


async def cache_client() -> CacheClient:
    return await get_cache_client()


async def query_engine() -> QueryEngine:
    return get_query_engine()


async def vector_store() -> VectorStore:
    return get_vector_store()


# Annotated type aliases for cleaner route signatures

DBClient = Annotated[DatabaseClient, Depends(db_client)]
CacheClient_ = Annotated[CacheClient, Depends(cache_client)]
Engine = Annotated[QueryEngine, Depends(query_engine)]
Store = Annotated[VectorStore, Depends(vector_store)]

# Startup / Shutdown


async def initialize_dependencies() -> None:
    db = await get_db_client()
    await db.connect()
    logger.info("Database client connected")

    await db.verify_pgvector()

    cache = await get_cache_client()
    await cache.connect()
    logger.info("Cache client connected")

    get_vector_store()
    get_query_engine()
    logger.info("QueryEngine and VectorStore initialized")


async def shutdown_dependencies() -> None:
    db = await get_db_client()
    await db.disconnect()
    logger.info("Database client disconnected")

    cache = await get_cache_client()
    await cache.disconnect()
    logger.info("Cache client disconnected")
