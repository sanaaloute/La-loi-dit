"""Refusal node: terminal node when the input guardrail blocks the query."""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.models import FinalAnswer
from backend.core.state import GraphState


async def refusal_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
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
