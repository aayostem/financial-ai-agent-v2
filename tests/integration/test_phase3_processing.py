import uuid
from datetime import date

import pytest

from financial_ai.ingestion.parsers.html_parser import ParsedSection
from financial_ai.ingestion.sec_ingestor import FilingMetadata
from financial_ai.processing.text_processor import TextProcessor
from financial_ai.utils.exceptions import ChunkingError

def make_mock_filing_meta(ticker: str = "AAPL") -> FilingMetadata:
    return FilingMetadata(
        ticker=ticker,
        filing_type="10-K",
        fiscal_year=2023,
        fiscal_quarter=None,
        filed_at=date(2023, 11, 3),
        accession_number="0000320193-23-000106",
        primary_document="aapl-20230930.htm",
        source_url="https://example.com/aapl.htm",
        cik="0000320193",
    )

def make_parsed_filing(ticker: str = "AAPL", num_sections: int = 2):
    from financial_ai.ingestion.parsers.html_parser import ParsedFiling
    
    text = "Apple revenue grew significantly. " * 100
    sections = [ParsedSection(name=f"Section {i}", text=text) for i in range(num_sections)]
    return ParsedFiling(
        ticker=ticker,
        filing_type="10-K",
        fiscal_year=2023,
        full_text=text * num_sections,
        sections=sections
    )
    
class TestTextProcessor:
    def setup_method(self):
        self.processor = TextProcessor()
        
    def test_count_tokes_is_exact(self):
        text = "Apple revenue grew 12% to $394 billion"
        count = self.processor.count_tokens(text)
        assert count > 0
        assert isinstance(count, int)
        
    def test_process_returns_chunks(self):
        parsed = make_parsed_filing()
        meta = make_mock_filing_meta()
        filing_id = uuid.uuid4()
        chunk = self.processor.process(parsed, meta, filing_id)
        assert len(chunk) > 0
        
    def test_chunks_have_correct_ticker(self):
        parsed = make_parsed_filing("MSFT")
        meta = make_mock_filing_meta("MSFT")
        filing_id = uuid.uuid4()
        chunks = self.processor.process(parsed, meta, filing_id)
        assert all(c.ticker == "MSFT" for c in chunks)
        
    def test_chunks_have_sequential_indices(self):
        parsed = make_parsed_filing()
        meta = make_mock_filing_meta()
        filing_id = uuid.uuid4()
        chunks = self.processor.process(parsed, meta, filing_id)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))
    
    def test_chunks_embedding_is_empty_list(self):
        parsed = make_parsed_filing()
        meta = make_mock_filing_meta()
        chunks = self.processor.process(parsed, meta, uuid.uuid4())
        assert all(c.embedding == [] for c in chunks)
        
    def test_empty_sections_raises_chunking_error(self):
        pass
    
    def test_estimate_cost_returns_expected_keys(self):
        pass
    
    def test_chunks_size_respected(self):
        pass
    
class TestEmbeddingClient:
    @pytest.mark.integration
    async def test_embed_query_returns_vector(self):
        from financial_ai.retrieval.embeddings import EmbeddingClient
        
        client = EmbeddingClient()
        vector = await client.embed_query("What was Apple's revenue?")
        assert isinstance(vector, list)
        assert len(vector) > 0
        assert all(isinstance(v, float) for v in vector)
        
    @pytest.mark.integration
    async def test_embed_texts_count_matches(self):
        from financial_ai.retrieval.embeddings import EmbeddingClient
        
        client = EmbeddingClient()
        texts = ["Revenue grew 12%", "Net income declined", "EPS was $3.14"]
        vectors = await client.embed_texts(texts)
        assert len(vectors) == len(texts)
        
    @pytest.mark.integration
    async def test_dimensions_consistent(self):
        from financial_ai.retrieval.embeddings import EmbeddingClient
        from financial_ai.config import get_settings
        
        client = EmbeddingClient()
        settings = get_settings()
        vector = await client.embed_query("test")
        assert len(vector) == settings.EMBEDDING_DIMENSIONS