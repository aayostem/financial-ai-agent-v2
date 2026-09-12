from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from financial_ai.config import get_settings

VALID_SECRETS = {
    "POSTGRES_PASSWORD": "test-pg-password-32-chars-minimum",
    "REDIS_PASSWORD": "test-redis-password-32-chars-min",
    "APP_ENV": "testing",
}


@pytest.fixture(autouse=True)
def _testing_env():
    with patch.dict(os.environ, VALID_SECRETS, clear=False):
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.connect = AsyncMock()
    db.disconnect = AsyncMock()
    db.verify_pgvector = AsyncMock()
    db.health_check = AsyncMock(
        return_value={
            "status": "healthy",
            "postgres_version": "16.0",
            "server_start_time": "2024-01-01",
            "pool_size": 2,
            "pool_checked_out": 0,
            "pool_overflow": -1,
        }
    )
    return db


@pytest.fixture
def mock_cache():
    cache = AsyncMock()
    cache.connect = AsyncMock()
    cache.disconnect = AsyncMock()
    cache.health_check = AsyncMock(
        return_value={
            "status": "healthy",
            "redis_version": "7.0",
            "used_memory_human": "1M",
            "pool_max_connections": 20,
        }
    )
    return cache


@pytest.fixture
def mock_query_engine():
    from financial_ai.retrieval.query_engine import QueryResult

    engine = AsyncMock()
    engine.query = AsyncMock(
        return_value=QueryResult(
            question="What is Apple's revenue?",
            answer="Apple reported $391 billion in revenue for FY2024.",
            analysis_style="analyst",
            search_type="similarity",
            agent_type="query_engine",
            latency_ms=500,
            source_documents=[],
        )
    )
    return engine


@pytest.fixture
def mock_vector_store():
    vs = AsyncMock()
    vs.stats = AsyncMock(
        return_value={
            "ticker": None,
            "total_chunks": 100,
            "total_filings": 5,
            "provider": "LocalEmbeddingProvider",
            "dimensions": 384,
        }
    )
    return vs


@pytest.fixture
async def test_client(mock_db, mock_cache, mock_query_engine, mock_vector_store):
    """
    AsyncClient against the FastAPI app with all dependencies mocked.
    Patches initialize_dependencies to skip real DB/cache connections.
    """
    from financial_ai.api.server import create_app

    app = create_app()

    with (
        patch(
            "financial_ai.api.dependencies.initialize_dependencies",
            AsyncMock(),
        ),
        patch(
            "financial_ai.api.dependencies.shutdown_dependencies",
            AsyncMock(),
        ),
        patch(
            "financial_ai.api.dependencies.get_db_client",
            AsyncMock(return_value=mock_db),
        ),
        patch(
            "financial_ai.api.dependencies.get_cache_client",
            AsyncMock(return_value=mock_cache),
        ),
        patch(
            "financial_ai.api.dependencies.get_query_engine",
            return_value=mock_query_engine,
        ),
        patch(
            "financial_ai.api.dependencies.get_vector_store",
            return_value=mock_vector_store,
        ),
        patch(
            "financial_ai.api.routes.get_cache_client",
            AsyncMock(return_value=mock_cache),
        ),
        patch(
            "financial_ai.api.routes.get_db_client",
            AsyncMock(return_value=mock_db),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


# =============================================================================
# Root endpoint
# =============================================================================


class TestRootEndpoint:
    @pytest.mark.asyncio
    async def test_root_returns_200(self, test_client):
        response = await test_client.get("/")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_root_returns_service_name(self, test_client):
        response = await test_client.get("/")
        data = response.json()
        assert "service" in data
        assert "version" in data


# =============================================================================
# Health endpoint
# =============================================================================


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, test_client):
        response = await test_client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_has_status(self, test_client):
        response = await test_client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")

    @pytest.mark.asyncio
    async def test_health_response_has_version(self, test_client):
        response = await test_client.get("/health")
        data = response.json()
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_response_has_services(self, test_client):
        response = await test_client.get("/health")
        data = response.json()
        assert "services" in data
        assert "database" in data["services"]
        assert "cache" in data["services"]

    @pytest.mark.asyncio
    async def test_health_service_has_healthy_field(self, test_client):
        response = await test_client.get("/health")
        data = response.json()
        for service in data["services"].values():
            assert "healthy" in service


# =============================================================================
# Query endpoint
# =============================================================================


class TestQueryEndpoint:
    @pytest.mark.asyncio
    async def test_query_returns_200(self, test_client):
        response = await test_client.post(
            "/query",
            json={"question": "What is Apple's revenue?", "ticker": "AAPL"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_query_response_has_answer(self, test_client):
        response = await test_client.post(
            "/query",
            json={"question": "What is Apple's revenue?"},
        )
        data = response.json()
        assert "answer" in data
        assert isinstance(data["answer"], str)

    @pytest.mark.asyncio
    async def test_query_response_has_source_documents(self, test_client):
        response = await test_client.post(
            "/query",
            json={"question": "What is Apple's revenue?"},
        )
        data = response.json()
        assert "source_documents" in data
        assert isinstance(data["source_documents"], list)

    @pytest.mark.asyncio
    async def test_query_response_has_latency(self, test_client):
        response = await test_client.post(
            "/query",
            json={"question": "Revenue?"},
        )
        data = response.json()
        assert "latency_seconds" in data
        assert data["latency_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_query_missing_question_returns_422(self, test_client):
        response = await test_client.post("/query", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_query_invalid_analysis_style_returns_422(self, test_client):
        response = await test_client.post(
            "/query",
            json={"question": "Revenue?", "analysis_style": "invalid_style"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_query_all_analysis_styles_accepted(self, test_client):
        for style in ("analyst", "executive", "risk"):
            response = await test_client.post(
                "/query",
                json={"question": "Revenue?", "analysis_style": style},
            )
            assert response.status_code == 200, f"Style {style} failed"

    @pytest.mark.asyncio
    async def test_query_all_search_types_accepted(self, test_client):
        for stype in ("similarity", "mmr", "hybrid"):
            response = await test_client.post(
                "/query",
                json={"question": "Revenue?", "search_type": stype},
            )
            assert response.status_code == 200, f"Search type {stype} failed"


# =============================================================================
# Ingest endpoint
# =============================================================================


class TestIngestEndpoint:
    @pytest.mark.asyncio
    async def test_ingest_returns_202(self, test_client):
        with patch("financial_ai.api.routes._ingest_background", AsyncMock()):
            response = await test_client.post(
                "/ingest/sec",
                json={"ticker": "AAPL", "filing_type": "10-K", "years": 1},
            )
        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_ingest_response_has_ticker(self, test_client):
        with patch("financial_ai.api.routes._ingest_background", AsyncMock()):
            response = await test_client.post(
                "/ingest/sec",
                json={"ticker": "MSFT", "filing_type": "10-K", "years": 1},
            )
        data = response.json()
        assert data["ticker"] == "MSFT"

    @pytest.mark.asyncio
    async def test_ingest_missing_ticker_returns_422(self, test_client):
        response = await test_client.post(
            "/ingest/sec",
            json={"filing_type": "10-K"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ingest_years_out_of_range_returns_422(self, test_client):
        response = await test_client.post(
            "/ingest/sec",
            json={"ticker": "AAPL", "years": 10},  # max is 5
        )
        assert response.status_code == 422


# =============================================================================
# Stats endpoint
# =============================================================================


class TestStatsEndpoint:
    @pytest.mark.asyncio
    async def test_global_stats_returns_200(self, test_client):
        response = await test_client.get("/stats")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_global_stats_has_required_fields(self, test_client):
        response = await test_client.get("/stats")
        data = response.json()
        assert "total_chunks" in data
        assert "total_filings" in data
        assert "provider" in data
        assert "dimensions" in data

    @pytest.mark.asyncio
    async def test_ticker_stats_returns_200(self, test_client):
        response = await test_client.get("/stats/AAPL")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_ticker_stats_has_ticker_field(self, test_client):
        response = await test_client.get("/stats/AAPL")
        data = response.json()
        assert "ticker" in data
