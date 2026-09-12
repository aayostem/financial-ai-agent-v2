from __future__ import annotations

import logging
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, Integer, SmallInteger, String, Text, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from financial_ai.storage.database import Base
from financial_ai.storage.repositories.base import BaseRepository
from financial_ai.utils.exceptions import DatabaseQueryError, VectorSearchError

logger = logging.getLogger(__name__)


# ORM Model
class FinancialChunk(Base):
    """
    ORM representation of the `financial_chunks` table.
    Mirrors create_schema.sql exactly — keep in sync.
    """

    __tablename__ = "financial_chunks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    filing_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)

    # Denormalised for hot-path queries — avoids joins
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    filing_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(SmallInteger)

    # Document structure
    section: Mapped[str | None] = mapped_column(String(100))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)

    # Vector embedding — dimensions set from settings at insert time
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)

    # Extracted data
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    entities: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    sentiment_score: Mapped[float | None] = mapped_column(Float)

    # Audit
    model_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="text-embedding-3-large"
    )

    def __repr__(self) -> str:
        return (
            f"<FinancialChunk id={self.id} ticker={self.ticker} "
            f"section={self.section} idx={self.chunk_index}>"
        )


# repository
class ChunksRepository(BaseRepository[FinancialChunk]):
    model_class = FinancialChunk

    async def similarity_search(
        self,
        query_embedding: list[float],
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        limit: int = 5,
        ef_search: int = 100,
    ) -> list[tuple[FinancialChunk, float]]:
        try:
            await self._session.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
            distance_col = FinancialChunk.embedding.cosine_distance(query_embedding)
            similarity_col = (1 - distance_col).label("similarity")

            stmt = select(FinancialChunk, similarity_col).order_by(distance_col).limit(limit)

            if ticker:
                stmt = stmt.where(FinancialChunk.ticker == ticker.upper())
            if filing_type:
                stmt = stmt.where(FinancialChunk.filing_type == filing_type)
            if fiscal_year:
                stmt = stmt.where(FinancialChunk.fiscal_year == fiscal_year)
            if section:
                stmt = stmt.where(FinancialChunk.section == section)

            result = await self._session.execute(stmt)
            rows = result.all()
            return [(row[0], float(row[1])) for row in rows]
        except Exception as exc:
            raise VectorSearchError(f"Vector similarity search failed: {exc}") from exc

    async def mmr_search(
        self,
        query_embedding: list[float],
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: int | None = None,
        limit: int = 5,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
    ) -> list[tuple[FinancialChunk, float]]:
        candidates = await self.similarity_search(
            query_embedding,
            ticker=ticker,
            filing_type=filing_type,
            fiscal_year=fiscal_year,
            limit=fetch_k,
        )
        if not candidates:
            return []

        selected: list[tuple[FinancialChunk, float]] = []
        remaining = list(candidates)

        while remaining and len(selected) < limit:
            best_idx = 0
            best_score = float("-inf")

            for i, (chunk, sim_score) in enumerate(remaining):
                if not selected:
                    mmr_score = sim_score
                else:
                    max_redundancy = max(
                        self._cosine_similarity(chunk.embedding, sel_chunk.embedding)
                        for sel_chunk, _ in selected
                    )
                    mmr_score = lambda_mult * sim_score - (1 - lambda_mult) * max_redundancy

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i

            selected.append(remaining.pop(best_idx))
        return selected

    async def bulk_upsert(self, chunks: list[FinancialChunk]) -> int:
        if not chunks:
            return 0

        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(FinancialChunk).values(
                [
                    {
                        "id": c.id,
                        "filing_id": c.filing_id,
                        "ticker": c.ticker,
                        "filing_type": c.filing_type,
                        "fiscal_year": c.fiscal_year,
                        "section": c.section,
                        "chunk_index": c.chunk_index,
                        "chunk_text": c.chunk_text,
                        "token_count": c.token_count,
                        "embedding": c.embedding,
                        "metrics": c.metrics,
                        "entities": c.entities,
                        "sentiment_score": c.sentiment_score,
                        "model_version": c.model_version,
                    }
                    for c in chunks
                ]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "chunk_text": stmt.excluded.chunk_text,
                    "embedding": stmt.excluded.embedding,
                    "metrics": stmt.excluded.metrics,
                    "entities": stmt.excluded.entities,
                    "sentiment_score": stmt.excluded.sentiment_score,
                    "model_version": stmt.excluded.model_version,
                },
            )

            result = await self._session.execute(stmt)
            await self._session.flush()
            logger.info("Bulk upserted %d chunks", len(chunks))
            return result.rowcount

        except Exception as exc:
            raise DatabaseQueryError(f"Bulk upsert of {len(chunks)} chunks failed: {exc}") from exc

    # filtered retrieval
    async def get_by_filing(
        self, filing_id: UUID, *, section: str | None = None
    ) -> list[FinancialChunk]:
        try:
            stmt = (
                select(FinancialChunk)
                .where(FinancialChunk.filing_id == filing_id)
                .order_by(FinancialChunk.chunk_index)
            )
            if section:
                stmt = stmt.where(FinancialChunk.section == section)

            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to fetch chunks for filing {filing_id}: {exc}"
            ) from exc

    async def count_by_ticker(self, ticker: str) -> int:
        try:
            result = await self._session.execute(
                select(func.count())
                .select_from(FinancialChunk)
                .where(FinancialChunk.ticker == ticker.upper())
            )
            return result.scalar_one()
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to count chunks for ticker '{ticker}': {exc}"
            ) from exc

    # INTERNAL

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in a) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
