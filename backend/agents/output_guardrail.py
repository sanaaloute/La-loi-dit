"""Output Guardrail Agent.

Final policy gate before the answer leaves the system.  Applies confidence
thresholds, unsafe-legal-advice detection and the disclaimer (full legal
disclaimer for high-impact question types or low-confidence / human-review
answers, short informational note otherwise — spec §33).
Uses the ``check_output`` and ``apply_confidence_policy`` tools.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
# Canonical disclaimer / note text lives in the prompt registry
# (backend/core/prompts.py) so operator overrides via ``prompts_dir`` apply.
# Re-exported here for backward compatibility with existing imports.
from backend.core.prompts import get_prompt

_DISCLAIMER_FR = get_prompt("DISCLAIMER_FR")
_DISCLAIMER_EN = get_prompt("DISCLAIMER_EN")
from backend.core.context import AppContext
from backend.core.models import FinalAnswer, QuestionType
from backend.core.state import GraphState

# Question types with a direct impact on the user's legal position (spec §33).
_HIGH_IMPACT_QUESTION_TYPES = frozenset({
    QuestionType.RIGHTS,
    QuestionType.OBLIGATIONS,
    QuestionType.PROCEDURE,
    QuestionType.CASE_ANALYSIS,
    QuestionType.CALCULATION,
    QuestionType.LEGAL_RULE,
})

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

        # Context-sensitive disclaimer (spec §33): full legal disclaimer for
        # high-impact question types, human-review or low-confidence answers;
        # short informational note otherwise.
        english = answer.language.startswith("en")
        full = get_prompt("DISCLAIMER_EN" if english else "DISCLAIMER_FR")
        note = get_prompt("INFO_NOTE_EN" if english else "INFO_NOTE_FR")
        if self._needs_full_disclaimer(state, answer, ctx):
            disclaimer = full
        else:
            disclaimer = note
        if full.strip() not in answer.answer and note.strip() not in answer.answer:
            answer.answer = answer.answer.rstrip() + disclaimer

        return {
            "final_answer": answer,
            "trace": [
                *state.get("trace", []),
                f"output_guardrail: {'REFUSED' if answer.refused else 'approved'}"
                f"{' (human review)' if answer.requires_human_review else ''}",
            ],
        }

    @staticmethod
    def _needs_full_disclaimer(state: GraphState, answer: FinalAnswer, ctx: AppContext) -> bool:
        plan = state.get("plan")
        question_type = plan.question_type if plan else QuestionType.GENERAL
        if question_type in _HIGH_IMPACT_QUESTION_TYPES:
            return True
        if answer.requires_human_review:
            return True
        threshold = getattr(ctx.settings, "confidence_threshold", 0.55)
        return bool(answer.evidence) and answer.confidence < threshold


output_guardrail_node = OutputGuardrailAgent().run
