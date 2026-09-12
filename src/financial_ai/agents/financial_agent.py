from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import AsyncOpenAI

from financial_ai.config import get_settings
from financial_ai.retrieval.document_retriever import RetrievalResult
from financial_ai.retrieval.query_engine import QueryEngine, QueryResult
from financial_ai.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """
    Complete result from an agent run.
    Richer than QueryResult — includes tool call trace and reasoning steps.
    """

    question: str
    answer: str
    analysis_style: str
    agent_type: str
    latency_ms: int
    source_documents: list[RetrievalResult] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning_steps: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def latency_seconds(self) -> float:
        return self.latency_ms / 1000.0

    def to_query_result(self) -> QueryResult:
        """Convert to QueryResult for API compatibility."""
        return QueryResult(
            question=self.question,
            answer=self.answer,
            analysis_style=self.analysis_style,
            search_type="agent",
            agent_type=self.agent_type,
            latency_ms=self.latency_ms,
            source_documents=self.source_documents,
            error=self.error,
        )


# SYSTEM PROMPT
_SYSTEM_PROMPTS: dict[str, str] = {
    "analyst": """You are a senior financial analyst with deep expertise in SEC filings, \
financial statements, and corporate strategy. You have access to a search tool that \
retrieves relevant passages from ingested SEC filings.

APPROACH:
1. Use the search tool to retrieve relevant information before answering
2. For complex questions, search multiple times with different queries
3. Synthesize findings into a precise, evidence-based answer
4. Always cite specific figures, dates, and document sections
5. If information is insufficient, say so explicitly — never speculate

Your answers should be suitable for institutional investment decisions.""",
    "executive": """You are a CFO-level advisor providing concise, decision-ready \
financial intelligence. You have access to a search tool over SEC filings.

APPROACH:
1. Search for the most relevant data points first
2. Lead with the single most important finding
3. Be direct and quantitative — numbers over adjectives
4. Flag any material risks or uncertainties
5. Keep responses concise but complete""",
    "risk": """You are a Chief Risk Officer conducting financial risk assessments. \
You have access to a search tool over SEC filings.

APPROACH:
1. Search specifically for risk factors, legal proceedings, and management discussion
2. Quantify risks where possible
3. Identify interconnected risk factors
4. Flag regulatory and litigation exposures explicitly
5. Assess risk trends (improving/deteriorating)""",
}

# =============================================================================
# Tool definitions for OpenAI function calling
# =============================================================================

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_filings",
            "description": (
                "Search SEC filings for information relevant to the query. "
                "Use this to retrieve specific financial data, risk factors, "
                "management discussion, or any other information from ingested documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant passages",
                    },
                    "ticker": {
                        "type": "string",
                        "description": "Optional: filter by company ticker (e.g. 'AAPL')",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "Optional: filter by document section. "
                            "Options: Risk Factors, MD&A, Business, Financial Statements, "
                            "Income Statement, Balance Sheet, Cash Flow"
                        ),
                    },
                    "fiscal_year": {
                        "type": "integer",
                        "description": "Optional: filter by fiscal year (e.g. 2024)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_filings",
            "description": (
                "Compare financial data across multiple filings or fiscal years. "
                "Use this when the question asks about trends, year-over-year changes, "
                "or comparisons between periods."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What metric or topic to compare",
                    },
                    "ticker": {
                        "type": "string",
                        "description": "Company ticker symbol",
                    },
                    "years": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of fiscal years to compare (e.g. [2022, 2023, 2024])",
                    },
                },
                "required": ["query", "ticker"],
            },
        },
    },
]


class FinancialAgent:
    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self._settings = get_settings()
        vs = vector_store or VectorStore()
        self._query_engine = QueryEngine(vector_store=vs)
        self._llm = self._build_llm_client()

    def _build_llm_client(self) -> AsyncOpenAI | None:
        if self._settings.MOCK_EXTERNAL_APIS:
            return None
        if not self._settings.OPENAI_API_KEY:
            logger.warning("FinancialAgent: OPENAI_API_KEY not set - falling back to QueryEngine")
            return None
        return AsyncOpenAI(
            api_key=self._settings.OPENAI_API_KEY.get_secret_value(),
            base_url=self._settings.LLM_BASE_URL or None,
            timeout=self._settings.LLM_REQUEST_TIMEOUT,
        )

    async def analyze(
        self,
        question: str,
        *,
        ticker: str | None,
        fiscal_year: int | None,
        analysis_style: Literal["analyst", "executive", "risk"] = "analyst",
        max_tool_calls: int = 5,
    ) -> AgentResult:
        t0 = time.monotonic()

        if self._llm is None:
            result = await self._query_engine.query(
                question, ticker=ticker, fiscal_year=fiscal_year, analysis_style=analysis_style
            )
            return AgentResult(
                question=question,
                answer=result.answer,
                analysis_style=analysis_style,
                agent_type="query_engine_fallback",
                latency_ms=int((time.monotonic() - t0) * 1000),
                source_documents=result.source_documents,
                error=result.error,
            )

        try:
            answer, sources, tool_calls, steps = await self._run_agent_loop(
                question=question,
                ticker=ticker,
                fiscal_year=fiscal_year,
                analysis_style=analysis_style,
                max_tool_calls=max_tool_calls,
            )

            return AgentResult(
                question=question,
                answer=answer,
                analysis_style=analysis_style,
                agent_type="query_engine_fallback",
                latency_ms=int((time.monotonic() - t0) * 1000),
                source_documents=sources,
                tool_calls=tool_calls,
                reasoning_steps=steps,
            )
        except Exception as exc:
            logger.error("Agent loop failed: %s", exc, exc_info=True)
            result = await self._run_agent_loop(
                question,
                ticker=ticker,
                fiscal_year=fiscal_year,
                analysis_style=analysis_style,
            )

            return AgentResult(
                question=question,
                answer=result.answer,
                analysis_style=analysis_style,
                agent_type="query_engine_fallback",
                latency_ms=int((time.monotonic() - t0) * 1000),
                source_documents=result.source_documents,
                error=str(exc),
            )

    async def _run_agent_loop(
        self,
        *,
        question: str,
        ticker: str | None,
        fiscal_year: int | None,
        analysis_style: str,
        max_tool_calls: int,
    ) -> tuple[str, list[RetrievalResult], list[dict], list[str]]:

        system_prompt = _SYSTEM_PROMPTS.get(analysis_style, _SYSTEM_PROMPTS["analysis"])
        context_hints = []
        if ticker:
            context_hints.append(f"Company: {ticker}")
        if fiscal_year:
            context_hints.append(f"Fiscal year: {fiscal_year}")
        if context_hints:
            user_content = f"[{', '.join(context_hints)}]\n\n{question}"
        else:
            user_content = question

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        all_sources: list[RetrievalResult] = []
        all_tool_calls: list[dict[str, Any]] = []
        reasoning_steps: list[str] = []
        tool_call_count = 0

        while tool_call_count < max_tool_calls:
            response = await self._llm.chat.completions.create(
                model=self._settings.LLM_MODEL,
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                temperature=self._settings.LLM_TEMPERATURE,
                max_tokens=self._settings.LLM_MAX_TOKENS,
            )

            message = response.choices[0].message

            # final answer
            if not message.tool_calls:
                return (message.content or "", all_sources, all_tool_calls, reasoning_steps)

            messages.append(message.model_dump(exclude_unset=True))

            for tc in message.tool_calls:
                tool_call_count += 1
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                logger.debug("Agent tool call: %s(%s)", fn_name, fn_args)
                reasoning_steps.append(f"Calling {fn_name}: {fn_args.get('query', '')}")

                tool_result, sources = await self._execute_tool(
                    fn_name, fn_args, ticker=ticker, fiscal_year=fiscal_year
                )
                all_sources.extend(sources)
                all_tool_calls.append(
                    {"tool": fn_name, "args": fn_args, "result": tool_result[:500]}
                )

                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
        logger.warning("Agent hit mx_tool_calls=%d, forcing final answer", max_tool_calls)
        final_response = await self._llm.chat.completions.create(
            model=self._settings.LLM_MODEL,
            messages=messages,
            temperature=self._settings.LLM_TEMPERATURE,
            max_tokens=self._settings.LLM_MAX_TOKENS,
        )
        return (
            final_response.choice[0].message.content or "",
            all_sources,
            all_tool_calls,
            reasoning_steps,
        )

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        ticker: str | None,
        fiscal_year: int | None,
    ) -> tuple[str, list[RetrievalResult]]:
        try:
            if tool_name == "search_filings":
                return await self._tool_search_filings(
                    args, default_ticker=ticker, default_year=fiscal_year
                )
            elif tool_name == "compare_filings":
                return await self._tool_compare_filings(args)
            else:
                return f"Unknown tool: {tool_name}", []
        except Exception as exc:
            logger.warning("Tool %s failed: %s", tool_name, exc)
            return f"Tool call failed: {exc}", []

    async def _tool_search_filings(
        self, args: dict[str, Any], *, default_ticker: str | None, default_year: int | None
    ) -> tuple[str, list[RetrievalResult]]:
        query = args.get("query", "")
        ticker = args.get("ticker") or default_ticker
        section = args.get("section")
        fiscal_year = args.get("fiscal_year") or default_year

        result = await self.query_engine.query(
            query,
            ticker=ticker,
            fiscal_year=fiscal_year,
            section=section if section else None,
            analysis_style="analyst",
            search_type="similarity",
        )

        if not result.source_documents:
            return "No relevant information found for this query.", []

        context_parts = [r.to_context_string() for r in result.source_documents]
        context = "\n\n---\n\n".join(context_parts)

        return context, result.source_documents

    async def _tool_compare_filings(
        self,
        args: dict[str, Any],
    ) -> tuple[str, list[RetrievalResult]]:
        query = args.get("query", "")
        ticker = args.get("ticker")
        years = args.get("years") or []

        all_sources: list[RetrievalResult] = []
        year_contexts: list[str] = []

        if years:
            for year in years[:3]:
                result = await self._query_engine.query(
                    query, ticker=ticker, fiscal_year=year, analysis_style="analyst"
                )
                if result.source_documents:
                    all_sources.extend(result.source_documents)
                    chunks = [r.to_context_string() for r in result.source_documents[:2]]
                    year_contexts.append("===FY(year) ===\n" + "\n".join(chunks))
        else:
            result = await self._query_engine.query(query, ticker=ticker, analysis_style="analyst")
            all_sources = result.source_documents
            year_contexts = [r.to_context_string() for r in all_sources]

        if not year_contexts:
            return "No Comparative data found.", []

        return "\n\n".join(year_contexts), all_sources
