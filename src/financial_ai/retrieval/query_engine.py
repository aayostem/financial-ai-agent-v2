from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from openai import AsyncOpenAI

from financial_ai.config import get_settings
from financial_ai.retrieval.document_retriever import DocumentRetriever, RetrievalResult
from financial_ai.retrieval.hybrid_search import HybridSearcher
from financial_ai.storage.vector_store import VectorStore
from financial_ai.utils.exceptions import RetrievalError

logger = logging.getLogger(__name__)

# SYSTEM PROMPT

_SYSTEM_PROMPTS: dict[str, str] = {
    "analyst": """You are a senior financial analyst with deep expertise in \
SEC filings, financial statements, and corporate strategy. Provide detailed, \
precise analysis grounded strictly in the provided context. Cite specific \
figures, dates, and sections. If the context does not contain sufficient \
information to answer the question, say so explicitly rather than speculating.""",
    "executive": """You are a CFO-level advisor providing concise, \
decision-ready financial insights. Synthesise the key points from the \
provided context into clear, actionable intelligence. Lead with the \
most important finding. Be direct and quantitative.""",
    "risk": """You are a chief risk officer conducting a thorough risk \
assessment. Analyse the provided context for financial risks, regulatory \
exposures, operational vulnerabilities, and forward-looking risk factors. \
Quantify risks where possible. Flag any material concerns explicitly.""",
}

_DEFAULT_STYLE = "analyst"

# Query result


@dataclass
class QueryResult:
    """
    Complete result from a RAG query.
    Returned by QueryEngine.query().
    Aligned with api/models.py QueryResponse.
    """

    question: str
    answer: str
    analysis_style: str
    search_type: str
    agent_type: str
    latency_ms: int
    source_documents: list[RetrievalResult] = field(default_factory=list)
    error: str | None = None

    @property
    def latency_seconds(self) -> float:
        return self.latency_ms / 1000.0

    def __repr__(self) -> str:
        return (
            f"<QueryResult style={self.analysis_style} "
            f"sources={len(self.source_documents)} "
            f"latency={self.latency_ms}ms>"
        )


# QUERY ENGINE
class QueryEngine:
    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self._settings = get_settings()
        vs = vector_store or VectorStore()
        self._retriever = DocumentRetriever(vs)
        self._hybrid = HybridSearcher()
        self._llm = self._build_llm_client()

    def _build_llm_client(self) -> AsyncOpenAI | None:
        if self._settings.MOCK_EXTERNAL_APIS:
            logger.info("QueryEngine: LLM calls mocked (MOCK_EXTERNAL_APIS=True)")
            return None

        # TRY GROQ FIRST
        if self._settings.GROQ_API_KEY:
            logger.debug("QueryEngine: Using GROQ_API_KEY for LLM")
            return AsyncOpenAI(
                api_key=self._settings.GROQ_API_KEY.get_secret_value(),
                base_url=self._settings.LLM_BASE_URL or "https://api.groq.com/openai/v1",
                timeout=self._settings.LLM_REQUEST_TIMEOUT,
            )

        # Use OpenAI
        if self._settings.OPENAI_API_KEY:
            logger.debug("QueryEngine: Using OPENAI_API_KEY for LLM (fallback)")
            return AsyncOpenAI(
                api_key=self._settings.OPENAI_API_KEY.get_secret_value(),
                base_url=self._settings.LLM_BASE_URL or "https://api.openai.com/v1",
                timeout=self._settings.LLM_REQUEST_TIMEOUT,
            )

        # NO API AVAILABLE
        logger.warning(
            "QueryEngine: No API Key available for LLM. "
            "Set GROQ_API_KEY (preferred) or OPENAI_API_KEY."
        )
        return None

    # PUBLIC API

    async def query(
        self,
        question: str,
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        analysis_style: str = _DEFAULT_STYLE,
        search_type: Literal["similarity", "mmr", "hybrid"] = "similarity",
        limit: int | None = None,
    ) -> QueryResult:
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        t0 = time.monotonic()

        if analysis_style not in _SYSTEM_PROMPTS:
            analysis_style = _DEFAULT_STYLE

        try:
            # step 1: retrieve
            results = await self._retrieve(
                question,
                ticker=ticker,
                filing_type=filing_type,
                fiscal_year=fiscal_year,
                section=section,
                search_type=search_type,
                limit=limit,
            )
            # step 2 auto upgrade to hybrid if vector confidence is low
            if (
                search_type == "similarity"
                and results
                and results[0].score < self._settings.VECTOR_SEARCH_THRESHOLD
            ):
                logger.info(
                    "Low vector confidence (%.3f < %.3f) - upgrading to hybrid",
                    results[0].score,
                    self._settings.VECTOR_SEARCH_THRESHOLD,
                )
                results = await self._hybrid.search(
                    question,
                    ticker=ticker,
                    filing_type=filing_type,
                    fiscal_year=fiscal_year,
                    limit=limit or self._settings.TOP_K_RESULTS,
                )
                search_type = "hybrid"

            if not results:
                return QueryResult(
                    question=question,
                    answer=(
                        "I could not find relevant information in the available "
                        "financial documents to answer this question. Please ensure "
                        "the relevant filings have been ingested."
                    ),
                    analysis_style=analysis_style,
                    search_type=search_type,
                    agent_type="query_engine",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    source_documents=[],
                )

            # step 3: build context
            context = self._retriever.build_context(results)

            # step 4: Generate answer
            answer = await self._generate_answer(
                question=question, context=context, analysis_style=analysis_style
            )

            latency_ms = int((time.monotonic() - t0) * 1000)

            logger.info(
                "Query complete - style=%s search=%s sources=%d latency=%dms",
                analysis_style,
                search_type,
                len(results),
                latency_ms,
            )

            return QueryResult(
                question=question,
                answer=answer,
                analysis_style=analysis_style,
                search_type=search_type,
                agent_type="query_engine",
                latency_ms=latency_ms,
                source_documents=results,
            )

        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.error("Query failed after %dms: %s", latency_ms, exc)
            return QueryResult(
                question=question,
                answer="",
                analysis_style=analysis_style,
                search_type=search_type,
                agent_type="query_engine",
                latency_ms=latency_ms,
                error=str(exc),
            )

    # INTERNAL
    async def _retrieve(
        self,
        question: str,
        *,
        ticker: str | None,
        filing_type: str | None,
        fiscal_year: int | None,
        section: str | None = None,
        search_type: str,
        limit: int | None,
    ) -> list[RetrievalResult]:
        if search_type == "hybrid":
            return await self._hybrid.search(
                question,
                ticker=ticker,
                filing_type=filing_type,
                fiscal_year=fiscal_year,
                limit=limit or self._settings.TOP_K_RESULTS,
            )

        return await self._retriever.retrieve(
            question,
            ticker=ticker,
            filing_type=filing_type,
            fiscal_year=fiscal_year,
            section=section,
            search_type=search_type,
            limit=limit,
        )

    async def _generate_answer(self, *, question: str, context: str, analysis_style: str) -> str:
        if self._llm is None:
            return f"[LLM unavailable - returning raw context]\n\n{context}"

        system_prompt = _SYSTEM_PROMPTS.get(analysis_style, _SYSTEM_PROMPTS[_DEFAULT_STYLE])

        user_message = (
            f"Using only the following financial document excerpts, "
            f"answer this question:\n\n"
            f"Question: {question}\n\n"
            f"Context: \n{context}"
        )

        try:
            response = await self._llm.chat.completions.create(
                model=self._settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=self._settings.LLM_TEMPERATURE,
                max_tokens=self._settings.LLM_MAX_TOKENS,
            )
            return response.choices[0].message.content or ""

        except Exception as exc:
            raise RetrievalError(f"LLM generation failed: {exc}") from exc
