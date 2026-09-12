from __future__ import annotations

import pytest

from financial_ai.agents.financial_agent import AgentResult, FinancialAgent
from financial_ai.config import get_settings
from financial_ai.retrieval.query_engine import QueryEngine, QueryResult
from financial_ai.storage.repositories.analysis import AnalysisRepository

class TestQueryResult:
    def test_latency_seconds_converts_correctly(self):
        result = QueryResult(
            question="test",
            answer="answer",
            analysis_style="analyst",
            search_type="similarity",
            agent_type="query_engine",
            latency_ms=1500
        )
        assert result.latency_seconds == 1.5
        
    def test_repr_includes_key_fields(self):
        result = QueryResult(
            question="test",
            answer="answer",
            analysis_style="analyst",
            search_type="similarity",
            agent_type="query_engine",
            latency_ms=250
        )
        assert "analyst" in repr(result)
        assert "250ms" in repr(result)
        
class TestAgentResult:
    def test_to_query_result_converts(self):
        agent_result = AgentResult(
            question="test",
            answer="answer",
            analysis_style="analyst",
            agent_type="financial_agent",
            latency_ms=800
        )
        qr = agent_result.to_query_result()
        assert isinstance(qr, QueryResult)
        assert qr.search_type == "agent"
        assert qr.latency_ms == 800
        
    def test_latency_seconds_property(self):
        result = QueryResult(
            question="test",
            answer="answer",
            analysis_style="analyst",
            agent_type="financial_agent",
            search_type="similarity",
            latency_ms=2000
        )
        assert result.latency_seconds == 2.0
        
class TestAnalysisRepository:
    @pytest.mark.integration
    async def test_record_and_retrieve(self, db_session):
        repo = AnalysisRepository(db_session)
        record = await repo.record(
            question="what was Apple's revenue?",
            answer="Apple's revenue was $394 billion.",
            agent_type="query_engine",
            latency_ms=320,
            ticker="AAPL",
            analysis_style="analyst",
            search_type="similarity",
        )
        assert record.id is not None
        assert record.ticker == "AAPL"
        assert record.latency_ms == 320
        
    @pytest.mark.integration
    async def test_get_by_ticker(self, db_session):
        repo = AnalysisRepository(db_session)
        records = await repo.get_by_ticker("AAPL")
        assert isinstance(records, list)
        
class TestQueryEngineMocked:
    @pytest.mark.integration
    async def test_query_returns_query_result(self, monkeypatch):
        monkeypatch.setenv("MOCK_EXTERNAL_APIS", "true")
        get_settings.cache_clear()
        
        engine = QueryEngine()
        result = await engine.query("What is Apple's revenue?", ticker="AAPL")
        assert isinstance(result, QueryResult)
        assert result.question == "What is Apple's revenue?"
        
    @pytest.mark.integration
    async def test_agent_falls_back_to_query_engine_without_key(self, monkeypatch):
        monkeypatch.setenv("MOCK_EXTERNAL_APIS", "true")
        get_settings.cache_clear()
        
        agent = FinancialAgent()
        assert agent._llm is None
        result = await agent.analyze("Test question?", ticker=None, fiscal_year=None)
        assert result.agent_type == "query_engine_fallback"
        

class TestErrorHandling:
    @pytest.mark.integration
    async def test_query_handles_empty_question(self):
        engine = QueryEngine()
        with pytest.raises(ValueError, match="Question cannot be empty"):
            await engine.query("", ticker="AAPL")
            
    @pytest.mark.integration
    async def test_agent_handles_empty_question(self, monkeypatch):
        monkeypatch.setenv("MOCK_EXTERNAL_APIS", "true")
        get_settings.cache_clear()
        
        agent = FinancialAgent()
        with pytest.raises(ValueError, match="Question cannot be empty"):
            await agent.analyze("", ticker=None, fiscal_year=None)
            
# fixtures

@pytest.fixture
async def db():
    from financial_ai.storage.database import DatabaseClient
    client = DatabaseClient()
    await client.connect()
    yield client
    await client.disconnect()
    
@pytest.fixture
async def db_session():
    from financial_ai.storage.database import get_db_client
    client = await get_db_client()
    await client.connect()
    async with client.session() as session:
        yield session
    await client.disconnect() 