"""Output guardrail: last policy gate before an answer leaves the system.

Enforces three deterministic rules:
  1. Refusal policy — an answer with zero evidence that does not already
     declare insufficient evidence is refused outright.
  2. Unsafe legal advice — matching content is kept but gets a warning and
     is escalated for human review.
  3. Citation integrity — every citation must be ``verified=True`` and
     reference a ``chunk_id`` present in the evidence; unverifiable
     citations are stripped and counted as hallucination suspects.
  4. Article-number soft check (spec §41) — article numbers cited in the
     prose that appear in no evidence chunk's ``article`` metadata raise a
     non-blocking "citation d'article non vérifiée" warning.
"""

from __future__ import annotations

import re

from backend.core.models import EvidenceChunk, FinalAnswer, RiskFlag
from backend.guardrails.policies import UNSAFE_LEGAL_ADVICE_PATTERNS

# Phrases with which an answer may legitimately declare it has no evidence.
_INSUFFICIENT_EVIDENCE_PHRASES = ("insuffisantes", "insufficient")

REFUSAL_REASON_NO_EVIDENCE = (
    "Aucune source vérifiable ne permet de répondre à cette question. "
    "Les éléments de preuve disponibles sont insuffisants."
)

# "article 542" / "articles L123-4" mentions in the answer prose.
# Includes Unicode dashes (U+2010–U+2015): LLMs often emit the non-breaking
# hyphen "853‑23", which must match the ASCII "853-23" in chunk metadata.
_ARTICLE_REF = re.compile(r"\barticles?\s+([A-Za-z]?\d[\w.\-‐‑‒–—―]*)", re.I)


def _normalize_article(ref: str) -> str:
    """Canonical form for comparing article numbers ("art. 542" -> "542")."""
    ref = re.sub(r"^(article|art)\.?\s*", "", ref.strip().lower())
    for dash in "‐‑‒–—―":
        ref = ref.replace(dash, "-")
    return ref.rstrip(".,;:)")


def flag_unverified_article_citations(answer: FinalAnswer, evidence: list[EvidenceChunk]) -> None:
    """Warn (never refuse) on article numbers absent from the evidence metadata.

    Heuristic by design: when no evidence chunk carries article metadata there
    is nothing to verify against and the check stays silent.
    """
    known = {_normalize_article(c.article) for c in evidence if c.article}
    known.discard("")
    if not known:
        return
    unknown: list[str] = []
    for match in _ARTICLE_REF.finditer(answer.answer):
        ref = _normalize_article(match.group(1))
        if ref and ref not in known and ref not in unknown:
            unknown.append(ref)
    for ref in unknown:
        if answer.language.startswith("en"):
            answer.warnings.append(f"unverified article citation: article {ref}")
        else:
            answer.warnings.append(f"citation d'article non vérifiée : article {ref}")


async def check_output(answer: FinalAnswer, evidence: list[EvidenceChunk], settings) -> FinalAnswer:
    """Apply the output policies to ``answer`` and return the (possibly
    modified) answer. Never raises."""
    effective_evidence = evidence if evidence else answer.evidence

    # --- 1. refusal policy: no evidence and no honest declaration ---
    if not effective_evidence and not answer.refused:
        lowered = answer.answer.lower()
        declares_insufficient = any(p in lowered for p in _INSUFFICIENT_EVIDENCE_PHRASES)
        if not declares_insufficient:
            answer.refused = True
            answer.refusal_reason = REFUSAL_REASON_NO_EVIDENCE
            answer.warnings.append("réponse refusée: aucune preuve vérifiable disponible")

    # --- 2. unsafe legal advice in the generated answer ---
    for pattern, reason in UNSAFE_LEGAL_ADVICE_PATTERNS:
        if pattern.search(answer.answer):
            answer.requires_human_review = True
            answer.warnings.append(f"contenu à risque détecté ({reason}): revue humaine requise")
            answer.metadata.setdefault("risk_flags", [])
            if RiskFlag.UNSAFE_LEGAL_ADVICE.value not in answer.metadata["risk_flags"]:
                answer.metadata["risk_flags"].append(RiskFlag.UNSAFE_LEGAL_ADVICE.value)
            break

    # --- 3. citation integrity ---
    evidence_ids = {c.chunk_id for c in effective_evidence}
    kept = []
    stripped = 0
    for citation in answer.citations:
        if citation.verified and citation.chunk_id and citation.chunk_id in evidence_ids:
            kept.append(citation)
        else:
            stripped += 1
    if stripped:
        answer.citations = kept
        answer.warnings.append(
            f"{stripped} citation(s) non vérifiable(s) retirée(s) de la réponse"
        )
        suspects = int(answer.metadata.get("hallucination_suspect_count", 0)) + stripped
        answer.metadata["hallucination_suspect_count"] = suspects
        answer.metadata.setdefault("risk_flags", [])
        if RiskFlag.HALLUCINATION_SUSPECT.value not in answer.metadata["risk_flags"]:
            answer.metadata["risk_flags"].append(RiskFlag.HALLUCINATION_SUSPECT.value)

    # --- 4. article-number soft check (warning only, never blocking) ---
    flag_unverified_article_citations(answer, effective_evidence)

    return answer
