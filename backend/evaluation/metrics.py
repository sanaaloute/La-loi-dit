"""Pure, offline evaluation metrics for the legal AI pipeline.

Every function is side-effect free and operates on the final answer / the
retrieved evidence only, so they can be unit-tested and reused outside the
evaluation runner.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from backend.core.models import EvidenceChunk, FinalAnswer

_CITATION_RE = re.compile(r"\[(\d+)\]")

# Phrases that legitimately appear without a citation marker (disclaimers,
# escalation advice). Statements containing them are not "ungrounded claims".
_DISCLAIMER_MARKERS = (
    "avertissement",
    "avis juridique",
    "recherche juridique",
    "professionnel du droit",
    "journal officiel",
    "à titre informatif",
    "a titre informatif",
    "ne constitue pas",
    "consulter",
    "consult",
    "disclaimer",
    "legal advice",
    "insuffisant",
    "insufficient",
)


def _normalize(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace for robust matching."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.split())


def _statements(answer_text: str) -> list[str]:
    """Split an answer into line/sentence-level statements."""
    parts = re.split(r"\n+|(?<=[.!?])\s+", answer_text or "")
    return [p.strip() for p in parts if p and p.strip()]


def _doc_matches(document_name: str, expected: str) -> bool:
    a, b = _normalize(document_name), _normalize(expected)
    return bool(a and b) and (a in b or b in a)


# ---------------------------------------------------------------------------
# Answer-level metrics
# ---------------------------------------------------------------------------


def citation_accuracy(final_answer: FinalAnswer) -> float:
    """Fraction of citations that were verified against retrieved evidence.

    Returns 1.0 when the answer carries no citations at all (nothing to be
    wrong about — an insufficient-evidence declaration is not penalized).
    """
    total = len(final_answer.citations)
    if total == 0:
        return 1.0
    verified = sum(1 for c in final_answer.citations if c.verified)
    return verified / total


def groundedness(final_answer: FinalAnswer) -> float:
    """Fraction of citation-marked statements backed by retrieved evidence.

    A statement is "backed" when every [n] marker it contains refers to an
    actual evidence chunk (1 <= n <= len(evidence)). Returns 1.0 when no
    statement carries a citation marker.
    """
    evidence = final_answer.evidence
    marked = [s for s in _statements(final_answer.answer) if _CITATION_RE.search(s)]
    if not marked:
        return 1.0
    backed = 0
    for statement in marked:
        indexes = {int(m.group(1)) for m in _CITATION_RE.finditer(statement)}
        if indexes and all(1 <= i <= len(evidence) for i in indexes):
            backed += 1
    return backed / len(marked)


def answer_relevance(answer_text: str, expected_keywords: Iterable[str]) -> float:
    """Fraction of expected keywords (accent-insensitive) present in the answer."""
    keywords = [k for k in expected_keywords if k and k.strip()]
    if not keywords:
        return 1.0
    normalized_answer = _normalize(answer_text)
    hits = sum(1 for k in keywords if _normalize(k) in normalized_answer)
    return hits / len(keywords)


# ---------------------------------------------------------------------------
# Retrieval-level metrics
# ---------------------------------------------------------------------------


def retrieval_precision(evidence: list[EvidenceChunk], expected_documents: Iterable[str]) -> float:
    """Fraction of retrieved chunks coming from an expected document.

    Empty evidence with non-empty expectations scores 0.0; empty expectations
    with empty evidence scores 1.0 (nothing wrong was retrieved).
    """
    expected = [d for d in expected_documents if d and d.strip()]
    if not evidence:
        return 1.0 if not expected else 0.0
    if not expected:
        return 0.0
    hits = sum(1 for c in evidence if any(_doc_matches(c.document_name, d) for d in expected))
    return hits / len(evidence)


def retrieval_recall(evidence: list[EvidenceChunk], expected_documents: Iterable[str]) -> float:
    """Fraction of expected documents present in the retrieved evidence."""
    expected = [d for d in expected_documents if d and d.strip()]
    if not expected:
        return 1.0
    found = sum(1 for d in expected if any(_doc_matches(c.document_name, d) for c in evidence))
    return found / len(expected)


# ---------------------------------------------------------------------------
# Hallucination heuristic
# ---------------------------------------------------------------------------


def hallucination_detected(final_answer: FinalAnswer) -> bool:
    """Heuristic hallucination flag.

    True when either:
    - the answer contains at least one unverified citation, or
    - the answer is evidence-backed but contains a substantive line
      (>= 8 words, not a header/disclaimer line) with no citation marker.

    Lines (not sentences) are the citation unit: a bullet quoting an excerpt
    carries its [n] marker at the end of the quote.
    """
    if any(not c.verified for c in final_answer.citations):
        return True
    if not final_answer.evidence:
        return False  # insufficient-evidence declarations make no claims
    for line in (final_answer.answer or "").splitlines():
        statement = line.strip().lstrip("-•* ").strip()
        words = statement.split()
        if len(words) < 8:
            continue
        if statement.rstrip().endswith(":"):
            continue  # header / lead-in lines
        lowered = _normalize(statement)
        if any(_normalize(marker) in lowered for marker in _DISCLAIMER_MARKERS):
            continue
        if not _CITATION_RE.search(line):
            return True
    return False
