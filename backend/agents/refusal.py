"""Refusal Agent.

Terminal node for blocked queries.  Returns a FinalAnswer with
`refused=True`, the guardrail flags and a bilingual explanation.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.core.context import AppContext
from backend.core.models import FinalAnswer
from backend.core.prompts import PromptRef, get_prompt
from backend.core.state import GraphState


class RefusalAgent(Agent):
    """Returns a refusal answer when the input guardrail blocks the query."""

    name = "refusal"
    # Resolved through the prompt registry (backend.core.prompts.REFUSAL_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("REFUSAL_SYSTEM")

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        guardrail = state.get("guardrail")
        reasons = "; ".join(guardrail.reasons) if guardrail else "policy violation"
        flags = ", ".join(f.value for f in guardrail.flags) if guardrail else "unknown"
        answer = FinalAnswer(
            answer=(
                f"{get_prompt('REFUSAL_FR').format(flags=flags)} / "
                f"{get_prompt('REFUSAL_EN').format(flags=flags)}"
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
