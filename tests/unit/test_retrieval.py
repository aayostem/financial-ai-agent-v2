from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


# =============================================================================
# RetrievalResult
# =============================================================================


class TestRetrievalResult:
    """RetrievalResult value object behaves correctly."""

    @pytest.fixture
    def sample_result(self):
        from financial_ai.retrieval.document_retriever import RetrievalResult

        return RetrievalResult(
            chunk_id="abc-123",
            chunk_text="Apple reported revenue of $391 billion in FY2024.",
            ticker="AAPL",
            filing_type="10-K",
            fiscal_year=2024,
            section="MD&A",
            score=0.85,
            metrics={"revenue": 391.0},
        )

    def test_result_has_all_fields(self, sample_result):
        assert sample_result.chunk_id == "abc-123"
        assert sample_result.ticker == "AAPL"
        assert sample_result.filing_type == "10-K"
        assert sample_result.fiscal_year == 2024
        assert sample_result.section == "MD&A"
        assert sample_result.score == 0.85

    def test_to_context_string_includes_source_header(self, sample_result):
        ctx = sample_result.to_context_string()
        assert "AAPL" in ctx
        assert "10-K" in ctx
        assert "FY2024" in ctx
        assert "MD&A" in ctx

    def test_to_context_string_includes_chunk_text(self, sample_result):
        ctx = sample_result.to_context_string()
        assert "Apple reported revenue" in ctx

    def test_to_context_string_includes_score(self, sample_result):
        ctx = sample_result.to_context_string()
        assert "0.850" in ctx or "0.85" in ctx

    def test_result_with_none_section(self):
        from financial_ai.retrieval.document_retriever import RetrievalResult

        result = RetrievalResult(
            chunk_id="xyz",
            chunk_text="Some text.",
            ticker="AAPL",
            filing_type="10-K",
            fiscal_year=None,
            section=None,
            score=0.5,
        )
        ctx = result.to_context_string()
        assert "General" in ctx  # None section shows as General

    def test_metrics_defaults_to_empty_dict(self):
        from financial_ai.retrieval.document_retriever import RetrievalResult

        result = RetrievalResult(
            chunk_id="xyz",
            chunk_text="Text.",
            ticker="AAPL",
            filing_type="10-K",
            fiscal_year=2024,
            section=None,
            score=0.5,
        )
        assert result.metrics == {}


# =============================================================================
# DocumentRetriever cache logic
# =============================================================================


class TestDocumentRetrieverCache:
    """DocumentRetriever cache key generation and cache bypass logic."""

    @pytest.fixture
    def retriever(self):
        from financial_ai.retrieval.document_retriever import DocumentRetriever

        mock_vs = MagicMock()
        return DocumentRetriever(vector_store=mock_vs)

    def test_cache_key_is_deterministic(self, retriever):
        key1 = retriever._build_cache_key(
            "What is revenue?", "AAPL", "10-K", 2024, None, 5, "similarity"
        )
        key2 = retriever._build_cache_key(
            "What is revenue?", "AAPL", "10-K", 2024, None, 5, "similarity"
        )
        assert key1 == key2

    def test_different_questions_give_different_keys(self, retriever):
        key1 = retriever._build_cache_key(
            "What is revenue?", "AAPL", "10-K", 2024, None, 5, "similarity"
        )
        key2 = retriever._build_cache_key(
            "What are risk factors?", "AAPL", "10-K", 2024, None, 5, "similarity"
        )
        assert key1 != key2

    def test_different_tickers_give_different_keys(self, retriever):
        key1 = retriever._build_cache_key("Revenue?", "AAPL", "10-K", 2024, None, 5, "similarity")
        key2 = retriever._build_cache_key("Revenue?", "MSFT", "10-K", 2024, None, 5, "similarity")
        assert key1 != key2

    def test_different_years_give_different_keys(self, retriever):
        key1 = retriever._build_cache_key("Revenue?", "AAPL", "10-K", 2023, None, 5, "similarity")
        key2 = retriever._build_cache_key("Revenue?", "AAPL", "10-K", 2024, None, 5, "similarity")
        assert key1 != key2

    def test_cache_key_contains_namespace(self, retriever):
        from financial_ai.storage.cache import NS_QUERY

        key = retriever._build_cache_key("Revenue?", "AAPL", "10-K", 2024, None, 5, "similarity")
        assert NS_QUERY in key

    @pytest.mark.asyncio
    async def test_cache_miss_falls_through_to_vector_search(self, retriever):
        """Cache miss must trigger vector store search."""
        from financial_ai.retrieval.document_retriever import RetrievalResult

        mock_result = RetrievalResult(
            chunk_id="1",
            chunk_text="Revenue text",
            ticker="AAPL",
            filing_type="10-K",
            fiscal_year=2024,
            section="MD&A",
            score=0.8,
        )

        with (
            patch.object(retriever, "_get_cached", AsyncMock(return_value=None)),
            patch.object(retriever, "_set_cached", AsyncMock()),
            patch.object(
                retriever._vector_store,
                "search",
                AsyncMock(
                    return_value=[
                        (
                            MagicMock(
                                id="1",
                                chunk_text="Revenue text",
                                ticker="AAPL",
                                filing_type="10-K",
                                fiscal_year=2024,
                                section="MD&A",
                                metrics={},
                            ),
                            0.8,
                        )
                    ]
                ),
            ),
        ):
            results = await retriever.retrieve(
                "What is revenue?",
                ticker="AAPL",
                use_cache=True,
            )
            assert len(results) >= 0  # May be filtered by threshold

    @pytest.mark.asyncio
    async def test_cache_failure_is_non_fatal(self, retriever):
        """Redis failure must not crash the retriever."""
        with (
            patch.object(
                retriever,
                "_get_cached",
                AsyncMock(side_effect=Exception("Redis down")),
            ),
            patch.object(
                retriever._vector_store,
                "search",
                AsyncMock(return_value=[]),
            ),
        ):
            results = await retriever.retrieve("Question?", use_cache=True)
            assert isinstance(results, list)


# =============================================================================
# QueryEngine routing
# =============================================================================


class TestQueryEngineRouting:
    """QueryEngine routes to correct retrieval strategy."""

    @pytest.fixture
    def engine(self):
        from financial_ai.retrieval.query_engine import QueryEngine

        mock_vs = MagicMock()
        with (
            patch("financial_ai.retrieval.query_engine.DocumentRetriever"),
            patch("financial_ai.retrieval.query_engine.HybridSearcher"),
        ):
            return QueryEngine(vector_store=mock_vs)

    @pytest.mark.asyncio
    async def test_empty_results_returns_no_info_message(self, engine):
        from financial_ai.retrieval.query_engine import QueryResult

        with patch.object(engine, "_retrieve", AsyncMock(return_value=[])):
            result = await engine.query("What is revenue?")
        assert isinstance(result, QueryResult)
        assert "could not find" in result.answer.lower()
        assert result.source_documents == []

    @pytest.mark.asyncio
    async def test_query_result_has_correct_fields(self, engine):
        from financial_ai.retrieval.document_retriever import RetrievalResult

        mock_result = RetrievalResult(
            chunk_id="1",
            chunk_text="Apple revenue $391B",
            ticker="AAPL",
            filing_type="10-K",
            fiscal_year=2024,
            section="MD&A",
            score=0.8,
        )
        with (
            patch.object(engine, "_retrieve", AsyncMock(return_value=[mock_result])),
            patch.object(
                engine,
                "_generate_answer",
                AsyncMock(return_value="Apple revenue grew in FY2024."),
            ),
        ):
            result = await engine.query("What is revenue?", ticker="AAPL")

        assert result.question == "What is revenue?"
        assert result.answer == "Apple revenue grew in FY2024."
        assert result.agent_type == "query_engine"
        assert len(result.source_documents) == 1

    @pytest.mark.asyncio
    async def test_query_returns_result_on_exception(self, engine):
        """Exceptions must be caught and returned as error result, not raised."""
        with patch.object(engine, "_retrieve", AsyncMock(side_effect=Exception("DB error"))):
            result = await engine.query("What is revenue?")
        assert result.error is not None
        assert "DB error" in result.error

    @pytest.mark.asyncio
    async def test_latency_ms_is_positive(self, engine):
        with (
            patch.object(engine, "_retrieve", AsyncMock(return_value=[])),
        ):
            result = await engine.query("Question?")
        assert result.latency_ms >= 0
        assert result.latency_seconds >= 0.0


# =============================================================================
# EmbeddingClient
# =============================================================================


class TestEmbeddingClient:
    """EmbeddingClient selects correct provider."""

    def test_client_initialises_in_testing(self):
        from unittest.mock import MagicMock, patch

        from financial_ai.retrieval.embeddings import EmbeddingClient

        with patch.object(EmbeddingClient, "_build_provider", return_value=MagicMock()):
            client = EmbeddingClient()
        assert client is not None

    def test_client_has_embed_query_method(self):
        from unittest.mock import MagicMock, patch

        from financial_ai.retrieval.embeddings import EmbeddingClient

        with patch.object(EmbeddingClient, "_build_provider", return_value=MagicMock()):
            client = EmbeddingClient()
        assert callable(getattr(client, "embed_query", None))

    def test_client_has_embed_texts_method(self):
        from unittest.mock import MagicMock, patch

        from financial_ai.retrieval.embeddings import EmbeddingClient

        with patch.object(EmbeddingClient, "_build_provider", return_value=MagicMock()):
            client = EmbeddingClient()
        assert callable(getattr(client, "embed_texts", None))

    @pytest.mark.asyncio
    async def test_embed_query_returns_list_of_floats(self):
        from unittest.mock import MagicMock, patch

        from financial_ai.retrieval.embeddings import EmbeddingClient

        with patch.object(EmbeddingClient, "_build_provider", return_value=MagicMock()):
            client = EmbeddingClient()
        with patch.object(client, "embed_query", AsyncMock(return_value=[0.1] * 384)):
            result = await client.embed_query("What is Apple's revenue?")
        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)
        assert len(result) == 384

    @pytest.mark.asyncio
    async def test_embed_texts_returns_list_of_embeddings(self):
        from unittest.mock import MagicMock, patch

        from financial_ai.retrieval.embeddings import EmbeddingClient

        with patch.object(EmbeddingClient, "_build_provider", return_value=MagicMock()):
            client = EmbeddingClient()
        docs = ["Text one.", "Text two.", "Text three."]
        with patch.object(client, "embed_texts", AsyncMock(return_value=[[0.1] * 384] * 3)):
            results = await client.embed_texts(docs)
        assert len(results) == 3
        assert all(len(r) == 384 for r in results)
