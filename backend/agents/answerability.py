"""Answerability gate (deterministic, no LLM call).

Detects abstention / insufficiency declarations in drafted answers so the
pipeline can:

1. trigger one corrective regeneration when the retrieved evidence DOES cover
   the question (the model abstained despite usable excerpts — response_generator);
2. cap the displayed confidence so a declared non-answer can never show a high
   score — a well-cited « les extraits ne couvrent pas… » used to reach 0.96
   because citation accuracy and answer-text coverage both rewarded it;
3. keep abstaining answers out of the answer cache (is_cacheable).

The detector is deliberately phrase-based and accent-insensitive: it must be
stable across the small models used for synthesis and cheap enough to run on
every draft.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional


def _fold(text: str) -> str:
    """Lowercase, unify apostrophes, strip accents — matching is language-noise free."""
    text = text.lower().replace("’", "'")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


# Phrases declaring that the sources do not cover the question (stored folded).
_ABSTENTION_PHRASES: tuple[str, ...] = tuple(
    _fold(p)
    for p in [
        # FR — model-written insufficiency declarations
        "n'est pas couverte",
        "ne sont pas couverts",
        "pas couverte par les extraits",
        "pas couverte par ces extraits",
        "ne permettent pas de répondre",
        "ne permet pas de répondre",
        "ne contiennent aucune disposition",
        "ne contiennent pas de disposition",
        "ne sont pas mentionnés",
        "ne sont pas mentionnées",
        "pas explicitement mentionné",
        "n'est pas mentionné",
        "ne figure pas dans les extraits",
        "ne figurent pas dans les extraits",
        "il est nécessaire de consulter",
        "il convient de consulter",
        "les sources fournies ne",
        "les extraits fournis ne",
        # FR — fixed pipeline messages (honest insufficiency paths)
        "preuves disponibles sont insuffisantes",
        "suffisamment d'informations fiables",
        # EN
        "not covered by the provided excerpts",
        "not covered by the excerpts",
        "do not contain any provision",
        "does not contain any provision",
        "insufficient to answer",
        "available evidence is insufficient",
        "not mentioned in the provided",
    ]
)

_CITATION_RE = re.compile(r"\[\d+\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# The mandated answer structure (RESPONSE_SECTIONS_ADDENDUM) includes an
# uncertainty section whose PURPOSE is to declare gaps. A cited, structured
# answer must not be punished for following that format: matches inside
# « ## Points d'incertitude » / « ## Uncertainties » are ignored. A flat
# refusal carries no such section, so it is still detected.
_ANY_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_UNCERTAINTY_TITLE_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|\*\*)?\s*(points? d'incertitudes?|incertitudes?|points? of uncertainty|uncertainties)\s*:?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)


def _strip_uncertainty_sections(text: str) -> str:
    """Remove the mandated uncertainty section (heading + body) from the text."""
    kept: list[str] = []
    in_uncertainty = False
    for line in text.splitlines():
        if _ANY_HEADING_RE.match(line) or _UNCERTAINTY_TITLE_RE.match(line):
            in_uncertainty = bool(_UNCERTAINTY_TITLE_RE.match(line))
            continue
        if not in_uncertainty:
            kept.append(line)
    return "\n".join(kept)


def has_abstention_language(text: str) -> bool:
    """True when the text declares (even partially) that sources lack the answer."""
    if not text:
        return False
    folded = _fold(text)
    return any(p in folded for p in _ABSTENTION_PHRASES)


@dataclass(frozen=True)
class AbstentionReport:
    """Detection result for one drafted answer.

    ``matched`` lists the abstention phrases found. ``has_affirmative_content``
    is true when at least one cited sentence is free of abstention language —
    the answer states SOMETHING grounded, making it a partial answer rather
    than a flat refusal.
    """

    matched: tuple[str, ...] = field(default_factory=tuple)
    has_affirmative_content: bool = False

    @property
    def declares_gap(self) -> bool:
        return bool(self.matched)

    @property
    def is_abstention(self) -> bool:
        """Flat refusal: a gap is declared and nothing affirmative is stated."""
        return bool(self.matched) and not self.has_affirmative_content


def detect_abstention(answer_text: str) -> AbstentionReport:
    """Scan a drafted answer for insufficiency declarations.

    The mandated « Points d'incertitude » section is excluded: its purpose is
    to declare gaps, so a structured, cited answer must not be capped for
    following the format. Matches elsewhere in the body still count.
    """
    if not answer_text:
        return AbstentionReport()
    body = _strip_uncertainty_sections(answer_text)
    folded = _fold(body)
    matched = tuple(p for p in _ABSTENTION_PHRASES if p in folded)
    if not matched:
        return AbstentionReport()
    has_affirmative = any(
        _CITATION_RE.search(sentence) and not has_abstention_language(sentence)
        for sentence in _SENTENCE_SPLIT_RE.split(body)
    )
    return AbstentionReport(matched=matched, has_affirmative_content=has_affirmative)


def confidence_cap(answer_text: str, settings: Any) -> Optional[float]:
    """Confidence ceiling implied by declared gaps; None when no gap is declared.

    - Flat refusal (nothing affirmative): cap at ``human_review_threshold``
      (0.40) — below ``confidence_threshold`` the output guardrail replaces the
      body with the honest low-confidence message.
    - Partial answer (some affirmative content but a declared gap): cap just
      under ``confidence_threshold`` (0.54) — the partial answer stays visible
      but can never display a high badge, nor enter the answer cache.
    """
    report = detect_abstention(answer_text)
    if not report.matched:
        return None
    if report.is_abstention:
        return float(settings.human_review_threshold)
    return max(0.0, float(settings.confidence_threshold) - 0.01)
