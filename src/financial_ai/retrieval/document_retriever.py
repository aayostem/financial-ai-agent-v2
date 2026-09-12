from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from financial_ai.config import get_settings
from financial_ai.storage.cache import NS_QUERY, build_key, get_cache_client
from financial_ai.storage.vector_store import VectorStore
from financial_ai.utils.exceptions import RetrievalError

if TYPE_CHECKING:
    from financial_ai.storage.repositories.chunks import FinancialChunk

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """
    A single retrieved chunk with its similarity score and provenance.
    Returned by DocumentRetriever.retrieve().
    """

    chunk_id: str
    chunk_text: str
    ticker: str
    filing_type: str
    fiscal_year: int | None
    section: str | None
    score: float
    metrics: dict[str, object] = field(default_factory=dict)

    def to_context_string(self) -> str:
        """
        Format this result as a context block for the LLM prompt.
        Includes source attribution so the model can cite its sources.
        """
        header = (
            f"[Source: {self.ticker} {self.filing_type} "
            f"FY{self.fiscal_year} — {self.section or 'General'} "
            f"(score={self.score:.3f})]"
        )
        return f"{header}\n{self.chunk_text}"

    @classmethod
    def from_chunk(cls, chunk: FinancialChunk, score: float) -> RetrievalResult:
        return cls(
            chunk_id=str(chunk.id),
            chunk_text=chunk.chunk_text,
            ticker=chunk.ticker,
            filing_type=chunk.filing_type,
            fiscal_year=chunk.fiscal_year,
            section=chunk.section,
            score=score,
            metrics=chunk.metrics or {},
        )


# DocumentRetriever
class DocumentRetriever:
    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self._settings = get_settings()
        self._vector_store = vector_store or VectorStore()

    async def retrieve(
        self,
        question: str,
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: str | None = None,
        section: str | None = None,
        limit: int | None = None,
        search_type: str = "similarity",
        use_cache: bool = True,
        score_threshold: float | None = None,
    ) -> list[RetrievalResult]:
        effective_limit = limit or self._settings.TOP_K_RESULTS
        effective_threshold = (
            score_threshold
            if score_threshold is not None
            else self._settings.VECTOR_SEARCH_THRESHOLD
        )

        # cache check
        cache_key = None
        if use_cache and not self._settings.MOCK_EXTERNAL_APIS:
            cache_key = self._build_cache_key(
                question, ticker, filing_type, fiscal_year, section, effective_limit, search_type
            )
            cached = await self._get_cached(cache_key)
            if cached is not None:
                logger.debug("Cache hit for query hash=%s", cache_key[-8:])
                return cached

        # vector search
        try:
            raw_results = await self._vector_store.search(
                question,
                ticker=ticker,
                filing_type=filing_type,
                fiscal_year=fiscal_year,
                section=section,
                limit=effective_limit,
                search_type=search_type,
            )
        except Exception as exc:
            raise RetrievalError(
                f"Vector search failed for question='{question[:50]}...':{exc}"
            ) from exc

        # score filtering
        results = [
            RetrievalResult.from_chunk(chunk, score)
            for chunk, score in raw_results
            if score >= effective_threshold
        ]

        logger.info(
            "Retrieved %d/%d results above threshold=%.2f for %s...",
            len(results),
            len(raw_results),
            effective_threshold,
            question[:50],
        )

        # cache population
        if use_cache and cache_key and results:
            await self._set_cached(cache_key, results)

        return results

    def build_context(
        self, results: list[RetrievalResult], *, max_tokens: int | None = None
    ) -> str:
        import tiktoken

        budget = max_tokens or self._settings.MAX_CONTEXT_TOKENS
        enc = tiktoken.get_encoding("cl100k_base")

        context_parts: list[str] = []
        tokens_used = 0

        for result in results:
            block = result.to_context_string()
            block_tokens = len(enc.encode(block))

            if tokens_used + block_tokens > budget:
                logger.debug(
                    "Context budget reached at %d/%d tokens after %d chunks",
                    tokens_used,
                    budget,
                    len(context_parts),
                )
                break

            context_parts.append(block)
            tokens_used += block_tokens

        return "\n\n---\n\n".join(context_parts)

    # INTERNAL

    def _build_cache_key(
        self,
        question: str,
        ticker: str | None,
        filing_type: str | None,
        fiscal_year: int | None,
        section: str | None,
        limit: int,
        search_type: str,
    ) -> str:
        payload = json.dumps(
            {
                "q": question,
                "t": ticker,
                "ft": filing_type,
                "fy": fiscal_year,
                "s": section,
                "l": limit,
                "st": search_type,
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return build_key(NS_QUERY, digest)

    async def _get_cached(self, cache_key: str) -> list[RetrievalResult] | None:
        try:
            client = await get_cache_client()
            data = await client.get(cache_key)
            if data is None:
                return None
            return [RetrievalResult(**item) for item in data]
        except Exception as exc:
            logger.warning("Cache GET failed (non-fatal): %s", exc)
            return None

    async def _set_cached(self, cache_key: str, results: list[RetrievalResult]) -> None:
        try:
            client = await get_cache_client()
            serializable = [
                {
                    "chunk_id": r.chunk_id,
                    "chunk_text": r.chunk_text,
                    "ticker": r.ticker,
                    "filing_type": r.filing_type,
                    "fiscal_year": r.fiscal_year,
                    "section": r.section,
                    "score": r.score,
                    "metrics": r.metrics,
                }
                for r in results
            ]
            await client.set(cache_key, serializable)
        except Exception as exc:
            logger.warning("Cache SET failed (non-fatal): %s", exc)
