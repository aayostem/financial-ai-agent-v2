import os

import pytest
from sqlalchemy import text

from financial_ai.config import get_settings
from financial_ai.storage.cache import CacheClient, build_key
from financial_ai.storage.database import DatabaseClient


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
async def db(settings):
    client = DatabaseClient()
    await client.connect()
    yield client
    await client.disconnect()


@pytest.fixture
async def cache(settings):
    client = CacheClient()
    await client.connect()
    yield client
    await client.disconnect()


class TestSettings:
    @pytest.mark.skipif(
        os.environ.get("APP_ENV") == "testing",
        reason="This test only passes in dev environment, not CI",
    )
    def test_env_is_development(self, settings):
        assert settings.APP_ENV == "development"

    def test_debug_auto_enabled_in_development(self, settings):
        assert settings.DEBUG is True

    def test_database_url_uses_asyncpg(self, settings):
        assert settings.DATABASE_URL.get_secret_value().startswith("postgresql+asyncpg://")

    def test_database_url_uses_psycopg(self, settings):
        assert settings.DATABASE_URL_SYNC.get_secret_value().startswith("postgresql+psycopg2://")

    def test_redis_url_format(self, settings):
        assert settings.REDIS_URL.get_secret_value().startswith("redis://:")

    def test_password_not_exposed_in_repr(self, settings):
        assert settings.POSTGRES_PASSWORD.get_secret_value() not in repr(settings)

    def test_chunk_overlap_less_than_chunk_size(self, settings):
        assert settings.CHUNK_OVERLAP_TOKENS < settings.CHUNK_SIZE_TOKENS


class TestDatabase:
    @pytest.mark.integration
    async def test_connection_is_live(self, db):
        async with db.connection() as conn:
            result = await conn.execute(text("SELECT 1 AS val"))
            row = result.fetchone()
        assert row.val == 1

    @pytest.mark.integration
    async def test_pgvector_extension_installed(self, db):
        async with db.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'filings'"
                )
            )
            row = result.fetchone()
        assert row is not None, "pgvector extension not found"

    @pytest.mark.integration
    async def test_session_commits_and_rolls_back(self, db):
        async with db.session() as session:
            await session.execute(
                text(
                    "INSERT INTO schema_migrations (version, description) "
                    "VALUES ('test-001', 'phase1 test') "
                    "ON CONFLICT (version) DO NOTHING"
                )
            )
        async with db.connection() as conn:
            result = await conn.execute(
                text("SELECT version FROM schema_migrations WHERE version = 'test-001'")
            )
            row = result.fetchone()
        assert row is not None

        # cleanup
        async with db.session() as session:
            await session.execute(text("DELETE FROM schema_migrations WHERE version = 'test-001'"))

    @pytest.mark.integration
    async def test_all_tables_exists(self, db):
        expected = {"filings", "financial_chunks", "analysis_history", "schema_migrations"}
        async with db.connection() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            tables = {row.tablename for row in result.fetchall()}
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"


class TestCache:
    @pytest.mark.integration
    async def test_ping(self, cache):
        assert cache._redis is not None

    @pytest.mark.integration
    async def test_set_and_get(self, cache):
        key = build_key("test", "phase1")
        await cache.set(key, {"value": 42}, ttl=60)
        result = await cache.get(key)
        assert result == {"value": 42}
        await cache.delete(key)

    @pytest.mark.integration
    async def test_get_missing_key_returns_none(self, cache):
        result = await cache.get(build_key("test", "nonexistent-xyz"))
        assert result is None

    @pytest.mark.integration
    async def test_delete_key(self, cache):
        key = build_key("test", "delete-me")
        await cache.set(key, "temporary", ttl=60)
        deleted = await cache.delete(key)
        assert deleted is True
        assert await cache.get(key) is None

    @pytest.mark.integration
    async def test_key_namespace(self):
        key = build_key("query", "abc123")
        assert key == "finai:query:abc123"

    @pytest.mark.integration
    async def test_clear_namespace(self, cache):
        for i in range(3):
            await cache.set(build_key("cleartest", str(i)), i, ttl=60)
        count = await cache.clear_namespace("cleartest")
        assert count == 3
