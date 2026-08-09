"""Base Agent classes with system prompts, tools and bounded execution.

There are two flavours:

- :class:`ToolCallingAgent` lets the LLM call tools in a bounded loop.
- :class:`Agent` is the abstract base; deterministic agents may override
  ``run`` directly and still expose their own ``system_prompt`` and ``tools``.

No agent here executes generated code or uses the sandbox.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Optional

from backend.agents.tools.base import ToolCall, ToolResult, as_function_schema, execute_tool_calls
from backend.agents.tools.registry import TOOL_REGISTRY
from backend.core.context import AppContext
from backend.core.state import GraphState


class Agent(ABC):
    """Abstract agent: every agent has a name, system prompt and allowed tools."""

    name: str = "agent"
    system_prompt: str = ""
    tools: list[Any] = []

    def __init__(self) -> None:
        if not self.name:
            self.name = self.__class__.__name__

    @abstractmethod
    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        """Run the agent and return the state keys it updates."""
        ...

    def _fallback(self, state: GraphState, reason: str) -> dict[str, Any]:
        """Minimal fallback that records an error and a trace entry."""
        return {
            "errors": [*state.get("errors", []), f"{self.name}: {reason}"],
            "trace": [*state.get("trace", []), f"{self.name}: fallback ({reason})"],
        }

    def _format_tool_history(self, history: list[tuple[ToolCall, ToolResult]]) -> str:
        if not history:
            return ""
        lines = ["\n--- Tool results ---"]
        for call, result in history:
            if result.error:
                lines.append(f"Tool {call.name}: ERROR {result.error}")
            else:
                output = result.output
                if isinstance(output, list):
                    output = f"[{len(output)} items]"
                elif isinstance(output, dict):
                    output = json.dumps(output, ensure_ascii=False, default=str)
                lines.append(
                    f"Tool {call.name}({json.dumps(call.arguments, ensure_ascii=False)}): {output}"
                )
        return "\n".join(lines)


class ToolCallingAgent(Agent):
    """Agent whose LLM can call tools in a bounded loop.

    Subclasses implement ``_build_user_message`` and ``_parse_final``.
    """

    max_tool_iterations: int = 3

    @abstractmethod
    def _build_user_message(self, state: GraphState) -> str:
        """Return the initial user message for this agent."""
        ...

    @abstractmethod
    def _parse_final(
        self,
        text: str,
        state: GraphState,
        ctx: AppContext,
        tool_history: list[tuple[ToolCall, ToolResult]],
    ) -> dict[str, Any]:
        """Convert the LLM's final text into state updates."""
        ...

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        """Run the bounded tool-calling loop and return state updates."""
        user_message = self._build_user_message(state)
        history: list[tuple[ToolCall, ToolResult]] = []
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        tool_schemas = [as_function_schema(t) for t in self.tools]

        for iteration in range(self.max_tool_iterations):
            try:
                output = await ctx.llm.complete_tools(
                    messages,
                    tool_schemas,
                    temperature=ctx.settings.llm_temperature,
                )
            except Exception as exc:
                return self._fallback(state, f"LLM tool call failed: {exc!r}")

            if output.final_text is not None:
                return self._parse_final(output.final_text, state, ctx, history)

            if not output.tool_calls:
                return self._fallback(state, "LLM returned neither final text nor tool calls")

            calls = [ToolCall(name=c["name"], arguments=c.get("arguments", {})) for c in output.tool_calls]
            results = await execute_tool_calls(TOOL_REGISTRY, calls, ctx, state)
            history.extend(zip(calls, results))

            assistant_message = json.dumps(
                [{"name": c.name, "arguments": c.arguments} for c in calls],
                ensure_ascii=False,
            )
            messages.append({"role": "assistant", "content": assistant_message})
            observation = self._format_tool_history(history)
            messages.append(
                {
                    "role": "user",
                    "content": f"Here are the tool results:{observation}\n\nNow produce the final answer or call more tools if needed.",
                }
            )

        return self._fallback(state, "tool iteration budget exhausted")


class CompletionAgent(Agent):
    """Agent whose LLM produces a single text output (no tool loop)."""

    @abstractmethod
    def _build_user_message(self, state: GraphState) -> str:
        """Return the user message for this agent."""
        ...

    @abstractmethod
    def _parse_final(
        self,
        text: str,
        state: GraphState,
        ctx: AppContext,
    ) -> dict[str, Any]:
        """Convert the LLM's text into state updates."""
        ...

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        try:
            text = await ctx.llm.complete(
                self.system_prompt,
                self._build_user_message(state),
                temperature=ctx.settings.llm_temperature,
            )
        except Exception as exc:
            return self._fallback(state, f"LLM completion failed: {exc!r}")
        return self._parse_final(text, state, ctx)
