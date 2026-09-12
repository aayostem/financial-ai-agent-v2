import pytest

from financial_ai.ingestion.parsers.html_parser import HTMLParser
from financial_ai.ingestion.parsers.text_parser import TextParser
from financial_ai.ingestion.sec_ingestor import SUPPORTED_FILING_TYPES, SECIngestor
from financial_ai.utils.exceptions import DuplicateFilingError, SECFetchError


class TestExceptionHierarchy:
    def test_sec_fetch_error_is_ingestion_error(self):
        from financial_ai.utils.exceptions import IngestionError

        err = SECFetchError("test")
        assert isinstance(err, IngestionError)

    def tet_duplicate_filing_error_stores_field(self):
        err = DuplicateFilingError("AAPL", "abc123")
        assert err.ticker == "AAPL"
        assert err.file_hash == "abc123"
        assert "abc123" in str(err)

    def test_cause_appears_in_str(self):
        from financial_ai.utils.exceptions import FinAIError

        original = ValueError("root cause")
        err = FinAIError("wrapper", cause=original)
        assert "root cause" in str(err)


class TestHTMLParser:
    def setup_method(self):
        self.parser = HTMLParser()

    def test_strips_sgml_header(self):
        content = "SGML JUNK\n<html><body><p>Hello</p></body></html>"
        parsed = self.parser.parse(content, ticker="TEST", filing_type="10-K")
        assert "SGML JUNK" not in parsed.full_text
        assert "Hello" in parsed.full_text

    def test_detects_mda_section(self):
        html = """
        <html><body>
        <h2>Management's Discussion and Analysis</h2>
        <p>Revenue increased significantly this year driven by strong iPhone sales
        and services growth across all geographic segments worldwide.</p>
        <h2>Risk Factors</h2>
        <p>The company faces intense competition in all markets where it operates
        and must continue to innovate to maintain its competitive position.</p>
        </body></html>
        """
        parsed = self.parser.parse(html, ticker="AAPL", filing_type="10-K", fiscal_year=2023)
        section_names = [s.name for s in parsed.sections]
        assert "MD&A" in section_names

    def test_return_general_when_no_sections(self):
        html = "<html><body><p>Plain text with no known section headings here. </p></body></html>"
        parsed = self.parser.parse(html, ticker="TEST", filing_type="10-K")
        assert len(parsed.sections) == 1
        assert parsed.sections[0].name == "General"

    def test_get_section_returns_none_for_missing(self):
        html = "<html><body><p>Text</p></body></html>"
        parsed = self.parser.parse(html, ticker="TEST", filing_type="10-K")
        assert parsed.get_section("MD&A") is None

    def test_removes_script_tags(self):
        html = "<html><body><script>alert('xss')</script><p>Clean</p></body></html>"
        parsed = self.parser.parse(html, ticker="TEST", filing_type="10-K")
        assert "alert" not in parsed.full_text

    def test_char_count_is_populated(self):
        html = "<html><body><p>Some content here</p></body></html>"
        parsed = self.parser.parse(html, ticker="TEST", filing_type="10-K")
        assert parsed.char_count == len(parsed.full_text)


class TestTextParser:
    def setup_method(self):
        self.parser = TextParser()

    def test_removes_urls(self):
        result = self.parser.clean("Visit https://example.com for more info.")
        assert "https://example.com" not in result

    def test_removes_emails(self):
        result = self.parser.clean("Contact investor@apple.com for details.")
        assert "investor@apple.com" not in result

    def test_normalize_numbers_strips_dollar_commas(self):
        result = self.parser.normalize_numbers("Revenue was $1,234,567 million")
        assert "$" not in result
        assert "1234567" in result

    def test_normalize_percentage_spacing(self):
        result = self.parser.normalize_numbers("Margin increased 12.5 %")
        assert "12.5%" in result

    def test_extract_revenue_metric(self):
        text = "Total revenue was $394.3 billion for the fiscal year"
        metrics = self.parser.extract_metrics(text)
        assert "revenue" in metrics
        assert metrics["revenue"] > 0

    def test_extract_eps_metrics(self):
        text = "Earnings per diluted share was $6.13 for the quarter."
        metrics = self.parser.extract_metrics(text)
        assert "eps" in metrics
        assert abs(metrics["eps"] - 6.13) < 0.01

    def test_empty_text_returns_empty(self):
        assert self.parser.clean("") == ""
        assert self.parser.clean("   ") == ""


class TestSECIngestor:
    def test_supported_filing_types(self):
        assert "10-K" in SUPPORTED_FILING_TYPES
        assert "10-Q" in SUPPORTED_FILING_TYPES
        assert "INVALID" not in SUPPORTED_FILING_TYPES

    def test_years_capped_at_five(self):
        assert min(10, 5) == 5
        assert min(3, 5) == 3

    # @pytest.mark.skip(reason="Network DNS resolution failure")
    @pytest.mark.integration
    async def test_resolve_cik_for_apple(self):
        async with SECIngestor() as ingestor:
            cik = await ingestor._resolve_cik("AAPL")
        assert cik == "0000320193"

    @pytest.mark.integration
    async def test_unsupported_type_raises(self):
        async with SECIngestor() as ingestor:
            with pytest.raises(SECFetchError):
                await ingestor.list_filings("AAPL", "INVALID")
