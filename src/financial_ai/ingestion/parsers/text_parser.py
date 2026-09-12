from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Patterns that are noise in financial text
_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(https?|ftp)://\S+", re.I),  # URLs
    re.compile(r"\S+@\S+\.\S+"),  # email addresses
    re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"),  # control characters
    re.compile(r"\n{4,}"),  # 4+ consecutive newlines
]

# Financial number normalisation — preserve structure, clean formatting
_NUMBER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Remove dollar signs before numbers: $1,234 → 1234
    (re.compile(r"\$\s*([\d,]+(?:\.\d+)?)"), r"\1"),
    # Remove commas in numbers: 1,234,567 → 1234567
    (re.compile(r"(\d),(\d{3})"), r"\1\2"),
    # Normalize percentages: 12.5 % → 12.5%
    (re.compile(r"(\d)\s+%"), r"\1%"),
]


class TextParser:
    def clean(self, text: str) -> str:
        if not text or not text.strip():
            return ""

        text = unicodedata.normalize("NFKC", text)

        for pattern in _NOISE_PATTERNS:
            text = pattern.sub(" ", text)

        lines = []
        for line in text.splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(line)

        cleaned_lines: list[str] = []
        blank_count = 0
        for line in lines:
            if not line:
                blank_count += 1
                if blank_count <= 2:
                    cleaned_lines.append("")
            else:
                blank_count = 0
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    def normalize_numbers(self, text: str) -> str:
        text = re.sub(r"\$\s*", "", text)
        text = text.replace(",", "")
        text = re.sub(r"(\d)\s+%", r"\1%", text)
        return text

    def extract_metrics(self, text: str) -> dict[str, float]:
        metrics: dict[str, float] = {}

        def _safe_float(raw: str) -> float | None:
            cleaned = raw.replace(",", "").strip()
            if not cleaned:
                return None
            try:
                return float(cleaned)
            except ValueError:
                return None

        revenue_match = re.search(
            r"(?:revenue|net\s+revenue|total\s+revenue)[^\d]*"
            r"([\d,]+(?:\.\d+)?)\s*"
            r"(billion|million|thousand)?",
            text,
            re.I,
        )
        if revenue_match:
            value = _safe_float(revenue_match.group(1))
            if value is not None:
                scale = revenue_match.group(2) or ""
                metrics["revenue"] = _apply_scale(value, scale)

        # Net income
        income_match = re.search(
            r"net\s+(?:income|earnings|loss)[^\d]*"
            r"([\d,]+(?:\.\d+)?)\s*"
            r"(billion|million|thousand)?",
            text,
            re.I,
        )
        if income_match:
            value = _safe_float(income_match.group(1))
            if value is not None:
                scale = income_match.group(2) or ""
                metrics["net_income"] = _apply_scale(value, scale)

        # EPS: "earnings per share of $X.XX" or "EPS of $X.XX"
        eps_match = re.search(
            r"(?:earnings\s+per\s+(?:diluted\s+)?share|eps)[^\d]*" r"\$?([\d]+(?:\.\d+)?)",
            text,
            re.I,
        )
        if eps_match:
            value = _safe_float(eps_match.group(1))
            if value is not None:
                metrics["eps"] = value

        # Operating margin: "operating margin of X%"
        margin_match = re.search(
            r"(?:operating|gross|net)\s+margin[^\d]*" r"([\d]+(?:\.\d+)?)\s*%",
            text,
            re.I,
        )
        if margin_match:
            value = _safe_float(margin_match.group(1))
            if value is not None:
                metrics["margin_pct"] = value

        return metrics


def _apply_scale(value: float, scale: str) -> float:
    """Convert a scaled value to its full numeric form."""
    scale = scale.lower()
    if scale == "billion":
        return value * 1_000_000_000
    if scale == "million":
        return value * 1_000_000
    if scale == "thousand":
        return value * 1_000
    return value
