"""Text-layer quality scoring for PDF extraction.

Some source PDFs (old Assemblée scans) carry a broken embedded OCR text
layer: garbled words ("Lel dilpolitionl"), missing spaces
("réglementationbancairelont"), orphan headings ("Article" with its number
lost on another line). Extracting that layer as-is poisons chunking and
retrieval, so such pages must be re-OCR'd instead.

Two complementary checks, both tuned on the corpus itself:

* **Trigram coverage** — fraction of the page's character trigrams present
  in a model trained on verified-clean French legal text
  (``french_trigrams.json``, regenerate with
  ``scripts/build_french_trigrams.py``). Broken OCR layers produce rare
  trigrams ("lpo", "onb", "sd u") and score < 0.90, while clean pages —
  including numeric budget tables — stay >= 0.93. The default threshold
  (0.92) sits between the two observed clusters.
* **Orphan article headings** — lines holding only "Article" (no number).
  Bad layers split "Article 1er" across lines ("Article\\nl'\\n"), which
  makes the chunker miss the article entirely. One orphan line is enough
  to flag the page.

Both checks are cheap (no model load at import; the trigram set is cached
on first use) and never raise.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).with_name("french_trigrams.json")

#: Below this many trigrams a page is too short to judge reliably.
_MIN_GRAMS = 50

#: A line holding only "Article"/"Art." (optionally with a colon) — its
#: number was lost elsewhere by a broken OCR layer.
_ORPHAN_ARTICLE_RE = re.compile(r"^\s*(?:article|art\.?)\s*:?\s*$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _trigram_model() -> frozenset[str]:
    """Load the bundled trigram set (empty set when the file is missing)."""
    try:
        grams = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("text-quality trigram model missing: %s", _MODEL_PATH)
        return frozenset()
    return frozenset(str(g) for g in grams)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def text_quality_score(text: str) -> float | None:
    """Fraction of the text's character trigrams known to the model.

    Returns ``None`` when the text is too short to judge (near-empty pages
    are the loader's "scanned" case, not a quality problem).
    """
    t = _normalize(text)
    gram_count = len(t) - 2
    if gram_count < _MIN_GRAMS:
        return None
    model = _trigram_model()
    if not model:
        return None  # no model: no opinion, the caller treats it as "ok"
    hits = sum(1 for i in range(gram_count) if t[i : i + 3] in model)
    return hits / gram_count


def has_orphan_article_heading(text: str) -> bool:
    """True when a line holds only "Article"/"Art." without a number."""
    return any(_ORPHAN_ARTICLE_RE.match(line) for line in text.splitlines())


def page_needs_ocr(text: str, min_score: float) -> bool:
    """Decide whether a page's embedded text layer is too broken to keep.

    ``min_score`` is the trigram-coverage threshold (see module docstring).
    """
    score = text_quality_score(text)
    if score is not None and score < min_score:
        return True
    return has_orphan_article_heading(text)
