"""Guardrail Agent (input side): prompt injection, jailbreak, sensitive
information, role hijacking and tool abuse detection before anything runs."""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.state import GraphState


async def input_guardrail_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    from backend.guardrails.input_guard import check_input

    result = await check_input(state["query"], ctx.settings)
    trace = [*state.get("trace", [])]
    if result.allowed:
        trace.append("input_guardrail: allowed")
    else:
        trace.append(f"input_guardrail: BLOCKED ({', '.join(f.value for f in result.flags)})")
    update: dict[str, Any] = {"guardrail": result, "trace": trace}
    if result.sanitized_query:
        update["query"] = result.sanitized_query
    return update
