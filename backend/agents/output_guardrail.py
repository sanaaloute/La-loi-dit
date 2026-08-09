"""Output Guardrail Agent.

Final policy gate before the answer leaves the system.  Applies confidence
thresholds, unsafe-legal-advice detection and the mandatory disclaimer.
Uses the ``check_output`` and ``apply_confidence_policy`` tools.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.core.context import AppContext
from backend.core.models import FinalAnswer
from backend.core.state import GraphState


_DISCLAIMER_FR = (
    "\n\n---\nAvertissement : cette réponse est une aide à la recherche juridique "
    "fondée sur les sources citées. Elle ne constitue pas un conseil juridique. "
    "Consultez un professionnel du droit pour votre situation particulière."
)

_DISCLAIMER_EN = (
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


class OutputGuardrailAgent(Agent):
    """Final safety and policy gate before returning the answer."""

    name = "output_guardrail"
    system_prompt = (
        "You are the output guardrail. Apply the mandatory legal disclaimer, confidence "
        "thresholds and safety checks to the final answer before it is returned to the user."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        answer: FinalAnswer = state["final_answer"]
        evidence = list(state.get("ranked_evidence", []))

        from backend.guardrails.output_guard import check_output

        answer = await check_output(answer, evidence, ctx.settings)

        lowered = (state.get("query", "") + " " + answer.answer).lower()
        if any(p in lowered for p in _HIGH_RISK_PATTERNS):
            answer.requires_human_review = True
            answer.warnings.append("demande à haut risque: revue par un expert juridique recommandée")

        # Apply confidence policy via the tool.
        conf_call = ToolCall(
            name="apply_confidence_policy",
            arguments={"confidence": answer.confidence, "has_evidence": bool(answer.evidence)},
        )
        conf_results = await execute_tool_calls(TOOL_REGISTRY, [conf_call], ctx, state)
        conf_result = conf_results[0]
        if not conf_result.error:
            policy = conf_result.output
            if policy.get("requires_human_review"):
                answer.requires_human_review = True
            answer.warnings.extend(policy.get("warnings", []))

        # Append disclaimer if not already present.
        disclaimer = _DISCLAIMER_EN if answer.language.startswith("en") else _DISCLAIMER_FR
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


output_guardrail_node = OutputGuardrailAgent().run
