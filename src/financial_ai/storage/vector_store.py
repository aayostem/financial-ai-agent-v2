from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from financial_ai.retrieval.embeddings import EmbeddingClient
from financial_ai.storage.database import get_db_client
from financial_ai.storage.repositories.chunks import ChunksRepository
from financial_ai.storage.repositories.filing import Filing, FilingsRepository

if TYPE_CHECKING:
    import uuid

    from financial_ai.ingestion.sec_ingestor import FilingMetadata
    from financial_ai.storage.repositories.chunks import FinancialChunk

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self) -> None:
        self._embedding_client = EmbeddingClient()
        logger.info(
            "VectorStore initialized - provider=%s dims=%d",
            self._embedding_client.provider_name,
            self._embedding_client.dimensions,
        )

    # Ingestion

    async def ingest(
        self, chunks: list[FinancialChunk], meta: FilingMetadata, file_hash: str
    ) -> tuple[uuid.UUID, int]:

        from financial_ai.utils.exceptions import DuplicateFilingError

        db = await get_db_client()

        # step 1: Deduplication check
        async with db.session() as session:
            filings_repo = FilingsRepository(session)
            if await filings_repo.exists_by_hash(file_hash):
                raise DuplicateFilingError(ticker=meta.ticker, file_hah=file_hash)

        logger.info(
            "Ingesting %s %s FY%s - %d chunks",
            meta.ticker,
            meta.filing_type,
            meta.fiscal_year,
            len(chunks),
        )

        # Step 2: Embed all chunks
        chunks_with_embeddings = await self._embedding_client.embed_chunks(chunks)

        # Step 3: Register filing
        import uuid as uuid_module

        filing_id = uuid_module.uuid4()

        async with db.session() as session:
            filings_repo = FilingsRepository(session)
            filing = Filing(
                id=filing_id,
                ticker=meta.ticker,
                filing_type=meta.filing_type,
                fiscal_year=meta.fiscal_year,
                fiscal_quarter=meta.fiscal_quarter,
                filed_at=meta.filed_at,
                source_url=meta.source_url,
                file_hash=file_hash,
                ingested_by="VectorStore",
                is_active=True,
            )
            await filings_repo.add(filing)

        # Step 4: Bulk upsert chunks
        for chunk in chunks_with_embeddings:
            chunk.filing_id = filing_id

        async with db.session() as session:
            chunks_repo = ChunksRepository(session)
            stored = await chunks_repo.bulk_upsert(chunks_with_embeddings)

        logger.info(
            "Ingestion complete - %s %s FY%s: filing_id=%s chunks=%d",
            meta.ticker,
            meta.filing_type,
            meta.fiscal_year,
            str(filing_id)[:8],
            stored,
        )
        return filing_id, stored

    # Search

    async def search(
        self,
        query: str,
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: int | None = None,
        section: str | None = None,
        limit: int = 5,
        search_type: str = "similarity",
        ef_search: int = 100,
    ) -> list[tuple[FinancialChunk, float]]:
        query_embedding = await self._embedding_client.embed_query(query)

        db = await get_db_client()
        async with db.session() as session:
            repo = ChunksRepository(session)

            if search_type == "mmr":
                return await repo.mmr_search(
                    query_embedding,
                    ticker=ticker,
                    filing_type=filing_type,
                    fiscal_year=fiscal_year,
                    limit=limit,
                )
            else:
                return await repo.similarity_search(
                    query_embedding,
                    ticker=ticker,
                    filing_type=filing_type,
                    fiscal_year=fiscal_year,
                    section=section,
                    limit=limit,
                    ef_search=ef_search,
                )

    # stats

    async def stats(self, ticker: str | None = None) -> dict[str, object]:
        db = await get_db_client()
        async with db.session() as session:
            chunks_repo = ChunksRepository(session)
            filings_repo = FilingsRepository(session)

            if ticker:
                chunk_count = await chunks_repo.count_by_ticker(ticker)
                filing_count = len(await filings_repo.get_by_ticker(ticker))
            else:
                chunk_count = await chunks_repo.count()
                filing_count = await filings_repo.count()

        return {
            "ticker": ticker or "all",
            "total_chunks": chunk_count,
            "total_filings": filing_count,
            "provider": self._embedding_client.provider_name,
            "dimensions": self._embedding_client.dimensions,
        }
