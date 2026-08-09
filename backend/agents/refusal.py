"""Refusal Agent.

Terminal node for blocked queries.  Returns a FinalAnswer with
`refused=True`, the guardrail flags and a bilingual explanation.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.core.context import AppContext
from backend.core.models import FinalAnswer
from backend.core.state import GraphState


class RefusalAgent(Agent):
    """Returns a refusal answer when the input guardrail blocks the query."""

    name = "refusal"
    system_prompt = (
        "You are the refusal agent. The user query violated safety policies. "
        "Return a clear, bilingual refusal with the guardrail flags and reasons."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        guardrail = state.get("guardrail")
        reasons = "; ".join(guardrail.reasons) if guardrail else "policy violation"
        flags = ", ".join(f.value for f in guardrail.flags) if guardrail else "unknown"
        answer = FinalAnswer(
            answer=(
                "Cette demande ne peut pas être traitée car elle enfreint les règles de "
                f"sécurité du système ({flags}). / This request cannot be processed because "
                f"it violates the system's safety policies ({flags})."
            ),
            confidence=0.0,
            refused=True,
            refusal_reason=reasons,
            language=state.get("language") or "fr",
        )
        return {
            "final_answer": answer,
            "trace": [*state.get("trace", []), f"refusal: {flags}"],
        }


refusal_node = RefusalAgent().run
