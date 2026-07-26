"""Output guardrail: last policy gate before an answer leaves the system.

Enforces three deterministic rules:
  1. Refusal policy — an answer with zero evidence that does not already
     declare insufficient evidence is refused outright.
  2. Unsafe legal advice — matching content is kept but gets a warning and
     is escalated for human review.
  3. Citation integrity — every citation must be ``verified=True`` and
     reference a ``chunk_id`` present in the evidence; unverifiable
     citations are stripped and counted as hallucination suspects.
"""

from __future__ import annotations

from backend.core.models import EvidenceChunk, FinalAnswer, RiskFlag
from backend.guardrails.policies import UNSAFE_LEGAL_ADVICE_PATTERNS

# Phrases with which an answer may legitimately declare it has no evidence.
_INSUFFICIENT_EVIDENCE_PHRASES = ("insuffisantes", "insufficient")

REFUSAL_REASON_NO_EVIDENCE = (
    "Aucune source vérifiable ne permet de répondre à cette question. "
    "Les éléments de preuve disponibles sont insuffisants."
)


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

    return answer
