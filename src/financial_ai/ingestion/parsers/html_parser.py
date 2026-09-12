from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_SECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"management.{0,20}discussion.{0,20}analysis", re.I), "MD&A"),
    (re.compile(r"risk\s+factors", re.I), "Risk Factors"),
    (
        re.compile(r"quantitative.{0,20}qualitative.{0,20}market\s+risk", re.I),
        "Market Risk",
    ),
    (re.compile(r"business\s+overview|item\s+1[.\s]+business", re.I), "Business"),
    (re.compile(r"financial\s+statements", re.I), "Financial Statements"),
    (re.compile(r"balance\s+sheet|financial\s+position", re.I), "Balance Sheet"),
    (
        re.compile(r"income\s+statement|results\s+of\s+operations", re.I),
        "Income Statement",
    ),
    (re.compile(r"cash\s+flow", re.I), "Cash Flow"),
    (re.compile(r"legal\s+proceedings", re.I), "Legal Proceedings"),
    (re.compile(r"properties", re.I), "Properties"),
    (re.compile(r"selected\s+financial\s+data", re.I), "Selected Financial Data"),
    (re.compile(r"notes\s+to\s+(the\s+)?financial", re.I), "Notes to Financials"),
]

# Tags that never contain useful text
_DISCARD_TAGS = frozenset(
    {
        "script",
        "style",
        "meta",
        "link",
        "head",
        "noscript",
        "svg",
        "img",
        "figure",
        "ix:nonfraction",
        "ix:nonnumeric",  # XBRL inline tags
        "xbrl",
        "xbrli",
    }
)
_MAX_BLANK_LINES = 2


@dataclass
class ParsedSection:
    name: str
    text: str
    char_count: int = field(init=False)

    def __post__int(self) -> None:
        self.char_count = len(self.text)

    def __repr__(self) -> str:
        return f"<ParsedSection '{self.name}' chars={self.char_count}>"


@dataclass
class ParsedFiling:
    ticker: str
    filing_type: str
    fiscal_year: int | None
    full_text: str
    sections: list[ParsedSection]
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.char_count = len(self.full_text)

    def get_section(self, name: str) -> str | None:
        for s in self.sections:
            if s.name == name:
                return s.text
        return None

    def __repr__(self) -> str:
        section_names = [s.name for s in self.sections]
        return (
            f"<ParsedFiling {self.ticker} {self.filing_type} "
            f"FY{self.fiscal_year} chars={self.char_count} "
            f"sections={section_names}>"
        )


# HTML PARSER
class HTMLParser:
    def parse(
        self, raw_html: str, *, ticker: str, filing_type: str, fiscal_year: int | None = None
    ) -> ParsedFiling:
        html_content = self._strip_sgml_header(raw_html)
        soup = BeautifulSoup(html_content, "lxml")
        self._remove_noise_tags(soup)
        full_text = self._extract_text(soup)
        sections = self._detect_sections(full_text)

        logger.debug(
            "Parsed %s %d - %d chars, %d sections",
            ticker,
            filing_type,
            len(full_text),
            len(sections),
        )
        return ParsedFiling(
            ticker=ticker,
            filing_type=filing_type,
            fiscal_year=fiscal_year,
            full_text=full_text,
            sections=sections,
        )

    def _strip_sgml_header(self, content: str) -> str:
        match = re.search(r"<html", content, re.I)
        if match:
            return content[match.start() :]
        return content

    def _remove_noise_tags(self, soup: BeautifulSoup) -> None:
        for tag_name in _DISCARD_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # remove empty tags
        for tag in soup.find_all(True):
            if isinstance(tag, Tag) and not tag.get_text(strip=True):
                tag.decompose()

    def _extract_text(self, soup: BeautifulSoup) -> str:
        raw_text = soup.get_text(separator="\n")
        lines = []
        for line in raw_text.splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(line)

        cleaned_lines: list[str] = []
        blank_count = 0
        for line in lines:
            if not line:
                blank_count += 1
                if blank_count <= _MAX_BLANK_LINES:
                    cleaned_lines.append("")
            else:
                blank_count = 0
                cleaned_lines.append(line)

        text = "\n".join(cleaned_lines).strip()

        text = self._remove_boilerplate(text)

        return text

    def _remove_boilerplate(self, text: str) -> str:
        patterns = [
            # Page numbers: "- 42 -" or "42"
            r"\n\s*-\s*\d+\s*-\s*\n",
            # Table of contents markers
            r"\.{5,}\s*\d+",
            # EDGAR filing header fields
            r"UNITED STATES\s+SECURITIES AND EXCHANGE COMMISSION.*?FORM\s+\S+",
            # Exhibit separators
            r"={10,}",
            r"-{10,}",
        ]
        for pattern in patterns:
            text = re.sub(pattern, "\n", text, flags=re.S)

        return text.strip()

    def _detect_sections(self, text: str) -> list[ParsedSection]:
        lines = text.splitlines()
        heading_positions: list[tuple[int, str]] = []

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(stripped) > 500:
                continue
            for pattern, section_name in _SECTION_PATTERNS:
                if pattern.search(stripped):
                    heading_positions.append((i, section_name))
                    break

        if not heading_positions:
            return [ParsedSection(name="General", text=text)]

        sections: list[ParsedSection] = []
        seen: set[str] = set()

        for idx, (line_idx, section_name) in enumerate(heading_positions):
            end_idx = (
                heading_positions[idx + 1][0] if idx + 1 < len(heading_positions) else len(lines)
            )
            section_text = "\n".join(lines[line_idx:end_idx]).strip()

            if section_name in seen or len(section_text) < 50:
                continue

            seen.add(section_name)
            sections.append(ParsedSection(name=section_name, text=section_text))

        return sections or [ParsedSection(name="General", text=text)]
