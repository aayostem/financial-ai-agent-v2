from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from financial_ai.config import get_settings
from financial_ai.retrieval.document_retriever import RetrievalResult
from financial_ai.retrieval.embeddings import EmbeddingClient
from financial_ai.storage.database import get_db_client
from financial_ai.storage.repositories.chunks import ChunksRepository
from financial_ai.utils.exceptions import RetrievalError

if TYPE_CHECKING:
    from financial_ai.storage.repositories.chunks import FinancialChunk

logger = logging.getLogger(__name__)

_RRF_K = 60


class HybridSearcher:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._embedding_client = EmbeddingClient()

    async def search(
        self,
        question: str,
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: int | None = None,
        limit: int | None = None,
        alpha: float | None = None,
    ) -> list[RetrievalResult]:
        effective_limit = limit or self._settings.TOP_K_RESULTS
        effective_alpha = alpha if alpha is not None else self._settings.HYBRID_SEARCH_ALPHA
        fetch_limit = effective_limit * 3

        import asyncio

        vector_task = self._vector_search(
            question,
            ticker=ticker,
            filing_type=filing_type,
            fiscal_year=fiscal_year,
            limit=fetch_limit,
        )
        text_task = self._text_search(
            question,
            ticker=ticker,
            filing_type=filing_type,
            fiscal_year=fiscal_year,
            limit=fetch_limit,
        )

        try:
            vector_results, text_results = await asyncio.gather(vector_task, text_task)
        except Exception as exc:
            raise RetrievalError(f"Hybrid search failed for '{question[:50]}': {exc}") from exc

        # Fuse results using RRF
        fused = self._reciprocal_rank_fusion(
            vector_results=vector_results,
            text_results=text_results,
            alpha=effective_alpha,
            limit=effective_limit,
        )

        logger.info(
            "Hybrid search - vector=%d text=%d fused=%d alpha=%.2f",
            len(vector_results),
            len(text_results),
            len(fused),
            effective_alpha,
        )
        return fused

    # vector search
    async def _vector_search(
        self,
        question: str,
        *,
        ticker: str | None,
        filing_type: str | None,
        fiscal_year: int | None,
        limit: int,
    ) -> list[tuple[FinancialChunk, float]]:
        query_embedding = await self._embedding_client.embed_query(question)

        db = await get_db_client()
        async with db.session() as session:
            repo = ChunksRepository(session)
            return await repo.similarity_search(
                query_embedding,
                ticker=ticker,
                filing_type=filing_type,
                fiscal_year=fiscal_year,
                limit=limit,
            )

    # Full search

    async def _text_search(
        self,
        question: str,
        *,
        ticker: str | None,
        filing_type: str | None,
        fiscal_year: int | None,
        limit: int,
    ) -> list[tuple[FinancialChunk, float]]:
        db = await get_db_client()
        async with db.session() as session:
            filters = []
            params: dict[str, object] = {"query": question, "limit": limit}
            if ticker:
                filters.append("ticker = :ticker")
                params["ticker"] = ticker.upper()
            if filing_type:
                filters.append("filing_type = :filing_type")
                params["filing_type"] = filing_type
            if fiscal_year:
                filters.append("fiscal_year = :fiscal_year")
                params["fiscal_year"] = fiscal_year

            where_clause = "WHERE " + " AND ".join(filters) if filters else ""
            sql = text(f"""
                SELECT
                    id,
                    similarity(chunk_text, :query) AS trgm_score
                FROM financial_chunks
                {where_clause}
                ORDER BY trgm_score DESC
                LIMIT :limit           
            """)

            try:
                result = await session.execute(sql, params)
                rows = result.fetchall()
            except Exception as exc:
                logger.warning(
                    "Full-text search failed (non-fatal, falling back to vector-only):%s", exc
                )
                return []

            if not rows:
                return []

            chunk_ids = [row[0] for row in rows]
            score_map = {row[0]: float(row[1]) for row in rows}

            from sqlalchemy import select

            from financial_ai.storage.repositories.chunks import FinancialChunk as ChunkModel

            chunks_result = await session.execute(
                select(ChunkModel).where(ChunkModel.id.in_(chunk_ids))
            )
            chunks = {c.id: c for c in chunks_result.scalars().all()}
            return [(chunks[cid], score_map[cid]) for cid in chunk_ids if cid in chunks]

    # RRF fusion

    def _reciprocal_rank_fusion(
        self,
        *,
        vector_results: list[tuple[FinancialChunk, float]],
        text_results: list[tuple[FinancialChunk, float]],
        alpha: float,
        limit: int,
    ) -> list[RetrievalResult]:
        vector_ranks: dict[str, int] = {
            str(chunk.id): rank + 1 for rank, (chunk, _) in enumerate(vector_results)
        }
        text_ranks: dict[str, int] = {
            str(chunk.id): rank + 1 for rank, (chunk, _) in enumerate(text_results)
        }

        all_ids: set[str] = set(vector_ranks) | set(text_results)

        chunk_lookup: dict[str, FinancialChunk] = {}
        for chunk, _ in vector_results:
            chunk_lookup[str(chunk.id)] = chunk
        for chunk, _ in text_results:
            chunk_lookup[str(chunk.id)] = chunk

        # compute fused RRF scores
        fused_scores: list[tuple[str, float]] = []
        for chunk_id in all_ids:
            vector_rrf = (
                1.0 / (_RRF_K + vector_ranks[chunk_id]) if chunk_id in vector_ranks else 0.0
            )
            text_rrf = 1.0 / (_RRF_K + text_ranks[chunk_id]) if chunk_id in text_ranks else 0.0
            fused_score = alpha * vector_rrf + (1 - alpha) * text_rrf
            fused_scores.append((chunk_id, fused_score))

        fused_scores.sort(key=lambda x: x[1], reverse=True)
        top = fused_scores[:limit]

        return [
            RetrievalResult.from_chunk(chunk_lookup[cid], score)
            for cid, score in top
            if cid in chunk_lookup
        ]
