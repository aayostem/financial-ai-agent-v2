from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# Enums


class AnalysisStyle(StrEnum):
    ANALYST = "analyst"
    EXECUTIVE = "executive"
    RISK = "risk"


class SearchType(StrEnum):
    SIMILARITY = "similarity"
    MMR = "mmr"
    HYBRID = "hybrid"


# Query


class QueryRequest(BaseModel):
    question: str = Field(..., description="The financial question to analyse")
    ticker: str | None = Field(default=None, description="Optional company ticker filter")
    filing_type: str | None = Field(
        default=None, description="Optional filing type filter (10-K, 10-Q)"
    )
    fiscal_year: int | None = Field(default=None, description="Optional fiscal year filter")
    analysis_style: AnalysisStyle = Field(default=AnalysisStyle.ANALYST)
    search_type: SearchType = Field(default=SearchType.SIMILARITY)
    limit: int | None = Field(default=None, ge=1, le=20)


class DocumentResponse(BaseModel):
    """A single source chunk returned with a query response."""

    chunk_id: str
    content: str
    ticker: str
    filing_type: str
    fiscal_year: int | None
    section: str | None
    score: float
    metrics: dict[str, Any] = {}


class QueryResponse(BaseModel):
    question: str
    answer: str
    analysis_style: str
    search_type: str
    agent_type: str
    latency_seconds: float
    source_documents: list[DocumentResponse] = []
    error: str | None = None


# Ingestion


class IngestionRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol (e.g. AAPL)")
    filing_type: str = Field(default="10-K", description="SEC form type")
    years: int = Field(default=2, ge=1, le=5, description="Years of filings to ingest")


class IngestionResponse(BaseModel):
    ticker: str
    filing_type: str
    filings_found: int
    chunks_stored: int
    success: bool
    skipped_duplicates: int = 0
    error: str | None = None


# Health


class ServiceStatus(BaseModel):
    """Status of a single backing service."""

    healthy: bool
    details: dict[str, Any] = {}


class HealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "unhealthy"
    version: str
    services: dict[str, ServiceStatus]  # db, cache, embeddings


# Stats


class StatsResponse(BaseModel):
    ticker: str | None
    total_chunks: int
    total_filings: int
    provider: str
    dimensions: int
