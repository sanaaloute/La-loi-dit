"""Text normalization and boilerplate removal.

Unicode NFKC normalization and whitespace cleanup preserve French accents
(é, è, ê, à, ç...) — NFKC only folds compatibility forms (ligatures,
full-width chars), never strips diacritics.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from backend.core.config import Settings, get_settings
from backend.ingestion.loaders import ExtractedDocument

# Lines that are almost always boilerplate rather than legal content.
_BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*page\s+\d+(\s*(/|de|sur|of)\s*\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*[-–—]\s*\d+\s*[-–—]\s*$"),
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),
]


def _settings() -> Settings:
    return get_settings()


def normalize_unicode(text: str) -> str:
    """NFKC-normalize and unify space characters; French accents stay intact."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace(" ", " ")
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs and more-than-two consecutive newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_boilerplate_line(line: str) -> bool:
    return any(pattern.match(line) for pattern in _BOILERPLATE_PATTERNS)


def strip_repeated_headers_footers(pages: list[str]) -> list[str]:
    """Remove first/last lines repeated across most pages (running heads)."""
    cfg = _settings()
    min_pages = cfg.text_cleaning_min_pages_for_header
    min_freq = cfg.text_cleaning_header_min_frequency
    if len(pages) < min_pages:
        return pages

    edge_lines: Counter[str] = Counter()
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        for candidate in {lines[0], lines[-1]} if lines else set():
            if len(candidate) >= 3:  # ignore single digits etc.
                edge_lines[candidate] += 1

    threshold = max(2, int(len(pages) * min_freq))
    repeated = {line for line, count in edge_lines.items() if count >= threshold}
    if not repeated:
        return pages

    cleaned = []
    for page in pages:
        lines = page.split("\n")
        while lines and lines[0].strip() in repeated:
            lines.pop(0)
        while lines and lines[-1].strip() in repeated:
            lines.pop()
        cleaned.append("\n".join(lines))
    return cleaned


def clean_text(text: str) -> str:
    """Normalize a single text block: unicode, boilerplate lines, whitespace."""
    text = normalize_unicode(text)
    lines = [ln for ln in text.split("\n") if not _is_boilerplate_line(ln.strip())]
    return normalize_whitespace("\n".join(lines))


def clean_pages(pages: list[str]) -> list[str]:
    """Clean each page, stripping repeated headers/footers first."""
    pages = [normalize_unicode(p) for p in pages]
    pages = strip_repeated_headers_footers(pages)
    return [clean_text(p) for p in pages]


def clean_document(doc: ExtractedDocument) -> ExtractedDocument:
    """Return a cleaned copy of an :class:`ExtractedDocument`."""
    pages = clean_pages(doc.pages) if doc.pages else []
    text = clean_text(doc.text) if not pages else "\n\n".join(p for p in pages if p)
    return doc.model_copy(update={"text": text, "pages": pages})
