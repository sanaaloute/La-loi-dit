"""Output Guardrail: final policy gate before the answer leaves the system.

Applies the refusal policy, confidence threshold, unsafe legal advice
detection, human-review escalation for low-confidence/high-risk answers,
and attaches the mandatory legal disclaimer.
"""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.models import FinalAnswer
from backend.core.state import GraphState

DISCLAIMER_FR = (
    "\n\n---\nAvertissement : cette réponse est une aide à la recherche juridique "
    "fondée sur les sources citées. Elle ne constitue pas un conseil juridique. "
    "Consultez un professionnel du droit pour votre situation particulière."
)
DISCLAIMER_EN = (
    "\n\n---\nDisclaimer: this answer is legal research assistance grounded in the "
    "cited sources. It is not legal advice. Consult a licensed legal professional "
    "for your specific situation."
)

_HIGH_RISK_PATTERNS = (
    "éviter une peine",
    "échapper à",
    "sans payer d'impôt",
    "fausse déclaration",
    "comment frauder",
    "avoid prosecution",
    "evade tax",
    "how to bribe",
)


async def output_guardrail_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    from backend.guardrails.output_guard import check_output

    answer: FinalAnswer = state["final_answer"]
    answer = await check_output(answer, state.get("ranked_evidence", []), ctx.settings)

    lowered = (state.get("query", "") + " " + answer.answer).lower()
    if any(p in lowered for p in _HIGH_RISK_PATTERNS):
        answer.requires_human_review = True
        answer.warnings.append("demande à haut risque: revue par un expert juridique recommandée")

    confidence_threshold = ctx.settings.confidence_threshold
    human_review_threshold = ctx.settings.human_review_threshold
    if not answer.refused:
        if answer.confidence < human_review_threshold and answer.evidence:
            answer.requires_human_review = True
            answer.warnings.append(
                f"confiance très faible ({answer.confidence:.0%}): escalade vers un expert juridique"
            )
        elif answer.confidence < confidence_threshold and answer.evidence:
            answer.warnings.append(f"confiance faible ({answer.confidence:.0%})")

    disclaimer = DISCLAIMER_EN if answer.language.startswith("en") else DISCLAIMER_FR
    if disclaimer.strip() not in answer.answer:
        answer.answer = answer.answer.rstrip() + disclaimer

    return {
        "final_answer": answer,
        "trace": [
            *state.get("trace", []),
            f"output_guardrail: {'REFUSED' if answer.refused else 'approved'}"
            f"{' (human review)' if answer.requires_human_review else ''}",
        ],
    }
