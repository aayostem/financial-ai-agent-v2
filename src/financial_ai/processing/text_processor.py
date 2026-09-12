from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tiktoken

from financial_ai.config import get_settings
from financial_ai.storage.repositories.chunks import FinancialChunk
from financial_ai.utils.exceptions import ChunkingError

if TYPE_CHECKING:
    from financial_ai.ingestion.parsers.html_parser import ParsedFiling
    from financial_ai.ingestion.sec_ingestor import FilingMetadata

logger = logging.getLogger(__name__)

_ENCODING_NAME = "cl100k_base"


@dataclass
class ChunkSpec:
    """
    Specifies how a single chunk should be created.
    Returned by _plan_chunks(), consumed by _build_chunk().
    """

    text: str
    section: str
    chunk_index: int
    token_count: int
    filing_id: uuid.UUID
    ticker: str
    filing_type: str
    fiscal_year: int | None
    metrics: dict[str, object] = field(default_factory=dict)
    entities: dict[str, object] = field(default_factory=dict)
    sentiment_score: float | None = None


class TextProcessor:
    def __init__(self) -> None:
        self._settings = get_settings()
        try:
            self._encoding = tiktoken.get_encoding(_ENCODING_NAME)
        except Exception as exc:
            raise ChunkingError(
                f"Failed to load tiktoken encoding '{_ENCODING_NAME}': {exc}"
            ) from exc

        self._chunk_size = self._settings.CHUNK_SIZE_TOKENS
        self._chunk_overlap = self._settings.CHUNK_OVERLAP_TOKENS

        logger.info(
            "TextProcessor initialized - chunk_size=%d overlap=%d encoding=%s",
            self._chunk_size,
            self._chunk_overlap,
            _ENCODING_NAME,
        )

    # PUBLIC API
    def process(
        self, parsed: ParsedFiling, meta: FilingMetadata, filing_id: uuid.UUID
    ) -> list[FinancialChunk]:
        if not parsed.sections:
            raise ChunkingError(
                f"ParsedFiling for {meta.ticker} {meta.filing_type} "
                f"FY{meta.fiscal_year} has no sections to chunk."
            )

        all_chunks: list[FinancialChunk] = []
        global_index = 0
        for section in parsed.sections:
            if not section.text or not section.text.strip():
                continue

            try:
                section_chunks = self._chunk_section(
                    text=section.text,
                    section_name=section.name,
                    filing_id=filing_id,
                    meta=meta,
                    start_index=global_index,
                )
            except Exception as exc:
                raise ChunkingError(
                    f"Failed to chunk section'{section.name}' for "
                    f"{meta.ticker} {meta.filing_type}: {exc}"
                ) from exc

            all_chunks.extend(section_chunks)
            global_index += len(section_chunks)

        logger.info(
            "Processed %s %s FY%s - %d sections -> %d chunks",
            meta.ticker,
            meta.filing_type,
            meta.fiscal_year,
            len(parsed.sections),
            len(all_chunks),
        )
        return all_chunks

    def count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def estimate_cost(
        self, chunks: list[FinancialChunk], *, cost_per_million_tokens: float = 0.13
    ) -> dict[str, float]:
        total_tokens = sum(c.token_count or 0 for c in chunks)
        cost = (total_tokens / 1_000_000) * cost_per_million_tokens
        return {
            "chunk_count": len(chunks),
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(cost, 4),
        }

    # INTERNAL

    def _chunk_section(
        self,
        text: str,
        section_name: str,
        filing_id: uuid.UUID,
        meta: FilingMetadata,
        start_index: int,
    ) -> list[FinancialChunk]:
        tokens = self._encoding.encode(text)

        if not tokens:
            return []

        if len(tokens) <= self._chunk_size:
            return [
                self._build_chunk(
                    text=text,
                    token_count=len(tokens),
                    section=section_name,
                    chunk_index=start_index,
                    filing_id=filing_id,
                    meta=meta,
                )
            ]

        chunks: list[FinancialChunk] = []
        start = 0
        chunk_index = start_index

        while start < len(tokens):
            end = min(start + self._chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]

            chunk_text = self._encoding.decode(chunk_tokens)

            if len(chunk_text.strip()) < 50:
                break

            chunks.append(
                self._build_chunk(
                    text=chunk_text,
                    token_count=len(chunk_tokens),
                    section=section_name,
                    chunk_index=chunk_index,
                    filing_id=filing_id,
                    meta=meta,
                )
            )

            chunk_index += 1

            step = self._chunk_size - self._chunk_overlap
            start += step

            if step <= 0:
                logger.warning("chunk_overlap >= chunk_size - processing single chunk only")
                break
        return chunks

    def _build_chunk(
        self,
        *,
        text: str,
        token_count: int,
        section: str,
        chunk_index: int,
        filing_id: uuid.UUID,
        meta: FilingMetadata,
    ) -> FinancialChunk:
        """
        Construct a FinancialChunk ORM instance from chunk parameters.

        The `embedding` field is intentionally left as an empty list —
        it will be populated by the embeddings module before DB insertion.
        """
        from financial_ai.ingestion.parsers.text_parser import TextParser

        text_parser = TextParser()

        # Extract metrics from this chunk's text
        metrics = text_parser.extract_metrics(text)

        return FinancialChunk(
            id=uuid.uuid4(),
            filing_id=filing_id,
            ticker=meta.ticker,
            filing_type=meta.filing_type,
            fiscal_year=meta.fiscal_year,
            section=section,
            chunk_index=chunk_index,
            chunk_text=text,
            token_count=token_count,
            embedding=[],  # populated by embeddings module
            metrics=metrics,
            entities={},  # populated by NER pipeline (future phase)
            sentiment_score=None,
            model_version=self._settings.EMBEDDING_MODEL,
        )
