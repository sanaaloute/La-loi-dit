"""Pure, offline evaluation metrics for the legal AI pipeline.

Every function is side-effect free and operates on the final answer / the
retrieved evidence only, so they can be unit-tested and reused outside the
evaluation runner.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

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
# Rank-aware retrieval metrics
#
# Ranked ids are matched against relevant ids with the same accent- and
# case-insensitive substring rule as the set-based metrics, so a golden
# document name does not need to be character-identical to the retrieved one.
# ---------------------------------------------------------------------------


def _hits_in_top_k(ranked_ids: Sequence[str], relevant: list[str], k: int) -> tuple[int, list[int]]:
    """Count relevant ids found in the top-k; also return their 1-based ranks.

    A relevant id counts once, at its best (lowest) rank. `k <= 0` means no
    position is inspected.
    """
    found = 0
    ranks: list[int] = []
    for rel in relevant:
        for rank, ranked_id in enumerate(ranked_ids[: max(0, k)], start=1):
            if _doc_matches(ranked_id, rel):
                found += 1
                ranks.append(rank)
                break
    return found, ranks


def _clean_relevant(relevant_ids: Iterable[str]) -> list[str]:
    """Drop blank relevant ids, preserving order and duplicates-free input."""
    seen: list[str] = []
    for item in relevant_ids:
        if item and item.strip() and item not in seen:
            seen.append(item)
    return seen


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: Iterable[str], k: int) -> float:
    """Fraction of relevant ids found within the top-k positions.

    Returns 1.0 when there is nothing relevant to find (consistent with
    :func:`retrieval_recall`), and 0.0 when relevant ids exist but ``k <= 0``
    or none of them appears in the top-k.
    """
    relevant = _clean_relevant(relevant_ids)
    if not relevant:
        return 1.0
    if k <= 0:
        return 0.0
    found, _ = _hits_in_top_k(ranked_ids, relevant, k)
    return found / len(relevant)


def precision_at_k(ranked_ids: Sequence[str], relevant_ids: Iterable[str], k: int) -> float:
    """Fraction of the top-k positions occupied by a relevant id.

    The denominator is ``min(k, len(ranked_ids))``: a system that retrieved
    fewer than k results is not penalized for the missing slots. Mirrors
    :func:`retrieval_precision` for empty inputs: an empty ranked list scores
    1.0 when there are no relevant ids and 0.0 otherwise.
    """
    relevant = _clean_relevant(relevant_ids)
    slots = min(max(0, k), len(ranked_ids))
    if slots == 0:
        return 1.0 if not relevant else 0.0
    if not relevant:
        return 0.0
    found, _ = _hits_in_top_k(ranked_ids, relevant, slots)
    return found / slots


def mrr(ranked_ids: Sequence[str], relevant_ids: Iterable[str]) -> float:
    """Mean reciprocal rank: 1/rank of the first relevant id, 0.0 if none.

    Returns 0.0 when the relevant set is empty (no relevant answer exists to
    rank) or when no relevant id appears anywhere in the ranked list.
    """
    relevant = _clean_relevant(relevant_ids)
    if not relevant:
        return 0.0
    best_rank: float | None = None
    for rank, ranked_id in enumerate(ranked_ids, start=1):
        if any(_doc_matches(ranked_id, rel) for rel in relevant):
            best_rank = float(rank)
            break
    return 1.0 / best_rank if best_rank else 0.0


def ndcg_at_k(ranked_ids: Sequence[str], relevant_ids: Iterable[str], k: int) -> float:
    """Normalized discounted cumulative gain at k with binary relevance.

    A position gains ``1 / log2(rank + 1)`` when it holds a relevant id;
    the ideal ranking places every relevant id first. Returns 1.0 when there
    is nothing relevant to rank (the ideal and actual lists agree trivially)
    and 0.0 when relevant ids exist but ``k <= 0`` or none is in the top-k.
    """
    relevant = _clean_relevant(relevant_ids)
    if not relevant:
        return 1.0
    if k <= 0:
        return 0.0
    dcg = 0.0
    matched: set[str] = set()
    for rank, ranked_id in enumerate(ranked_ids[:k], start=1):
        for rel in relevant:
            if rel not in matched and _doc_matches(ranked_id, rel):
                dcg += 1.0 / math.log2(rank + 1)
                matched.add(rel)
                break
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


# ---------------------------------------------------------------------------
# Issue coverage (per-case answer completeness)
# ---------------------------------------------------------------------------


def issue_coverage(
    answer_text: str,
    expected_issues: Iterable[Mapping[str, Any]],
) -> tuple[float, list[str]]:
    """Fraction of expected issue categories covered by the answer text.

    Each expected issue is a mapping with a ``category`` name and a
    ``keywords`` list; the issue counts as covered when at least one of its
    keywords appears in the answer (accent/case-insensitive substring, same
    normalization as the other metrics). Returns the coverage ratio and the
    names of the uncovered categories — the evaluation of a case must fail
    when this list is non-empty (e.g. an answer discussing only tribunal
    jurisdiction misses dismissal grounds, notice, compensation, ...).
    Returns ``(1.0, [])`` when no issues are declared.
    """
    issues = [i for i in expected_issues if i.get("category")]
    if not issues:
        return 1.0, []
    normalized_answer = _normalize(answer_text)
    missing: list[str] = []
    for issue in issues:
        keywords = [k for k in issue.get("keywords", []) if k and k.strip()]
        if keywords and any(_normalize(k) in normalized_answer for k in keywords):
            continue
        missing.append(str(issue["category"]))
    return (len(issues) - len(missing)) / len(issues), missing


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
