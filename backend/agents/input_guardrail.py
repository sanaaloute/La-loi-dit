"""Input Guardrail Agent.

Detects prompt injection, jailbreak, sensitive information, role hijacking and
tool abuse before anything runs.  Uses the ``check_input`` tool.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.core.context import AppContext
from backend.core.state import GraphState


class InputGuardrailAgent(Agent):
    """First-line safety gate."""

    name = "input_guardrail"
    system_prompt = (
        "You are the input guardrail of a legal research assistant for Burkina Faso. "
        "Inspect the user query for safety issues and decide whether the pipeline may continue."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        call = ToolCall(name="check_input", arguments={"query": state["query"]})
        results = await execute_tool_calls(TOOL_REGISTRY, [call], ctx, state)
        result = results[0]
        if result.error:
            # Fail closed: block the query if the tool cannot run.
            from backend.core.models import GuardrailResult
            return {
                "guardrail": GuardrailResult(
                    allowed=False,
                    flags=["tool_abuse"],
                    reasons=[f"input guardrail tool failed: {result.error}"],
                ),
                "trace": [*state.get("trace", []), "input_guardrail: tool error, blocked"],
                "errors": [*state.get("errors", []), f"input_guardrail: {result.error}"],
            }

        from backend.core.models import GuardrailResult

        output = result.output
        allowed = bool(output.get("allowed"))
        trace = [*state.get("trace", [])]
        if allowed:
            trace.append("input_guardrail: allowed")
        else:
            flags = output.get("flags", [])
            trace.append(f"input_guardrail: BLOCKED ({', '.join(flags)})")

        guardrail = GuardrailResult(**output)
        update: dict[str, Any] = {
            "guardrail": guardrail,
            "trace": trace,
        }
        if guardrail.sanitized_query:
            update["query"] = guardrail.sanitized_query
        return update


# Graph compatibility wrapper.
input_guardrail_node = InputGuardrailAgent().run
