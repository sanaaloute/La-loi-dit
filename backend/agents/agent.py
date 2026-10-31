"""Base Agent classes with system prompts, tools and bounded execution.

There are two flavours:

- :class:`ToolCallingAgent` lets the LLM call tools in a bounded loop.
- :class:`Agent` is the abstract base; deterministic agents may override
  ``run`` directly and still expose their own ``system_prompt`` and ``tools``.

No agent here executes generated code or uses the sandbox.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Optional

from backend.agents.tools.base import ToolCall, ToolResult, as_function_schema, execute_tool_calls
from backend.agents.tools.registry import TOOL_REGISTRY
from backend.core.context import AppContext
from backend.core.exceptions import LLMError
from backend.core.state import GraphState, emit_stream_event


#: Answer drafts are held back until this many content chars accumulated, so a
#: draft opening like a JSON envelope or code fence is never streamed live —
#: the corrective passes then rewrite it before anything reaches the UI.
_ANSWER_GUARD_CHARS = 80


async def streamed_completion(
    llm: Any,
    system: str,
    user: str,
    state: GraphState,
    *,
    node: str,
    emit_answer: bool = False,
    **kwargs: Any,
) -> str:
    """Complete a prompt, streaming tokens to the API layer when possible.

    On streaming runs (``state["stream_queue"]`` set by the SSE/WS layer),
    reasoning chunks are forwarded as ``reasoning_delta`` events attributed to
    ``node``; answer chunks are forwarded as ``delta`` events only when
    ``emit_answer`` is set (the response generator — intermediate agents'
    content must not leak into the answer stream). Answer content is only
    streamed once the opening chars confirm prose: a draft starting like a
    JSON envelope (``{`` / ```` ``` ````) is suppressed so raw structured output
    never appears in the answer bubble. Falls back to the non-streaming
    ``complete()`` when there is no queue, no streaming support, or the stream
    fails mid-way (a ``delta_reset`` then tells the UI to drop the partial
    provisional text).
    """
    if state.get("stream_queue") is None or not hasattr(llm, "complete_stream"):
        return await llm.complete(system, user, **kwargs)
    parts: list[str] = []
    pending = ""  # answer chars held back pending the prose/JSON decision
    json_like = False  # confirmed structured draft: never stream it live
    try:
        async for kind, text in llm.complete_stream(system, user, **kwargs):
            if kind == "reasoning":
                emit_stream_event(state, "reasoning_delta", node=node, text=text)
                continue
            parts.append(text)
            if not emit_answer or json_like:
                continue
            pending += text
            stripped = pending.lstrip()
            if stripped.startswith(("{", "```")):
                # Undecided while short; confirmed JSON-ish once past the guard.
                if len(stripped) >= _ANSWER_GUARD_CHARS:
                    json_like = True
                continue
            if pending:
                emit_stream_event(state, "delta", text=pending)
                pending = ""
        # Short prose answers never crossed the guard mid-stream: flush now.
        if emit_answer and pending and not json_like:
            emit_stream_event(state, "delta", text=pending)
        return "".join(parts)
    except LLMError:
        if emit_answer:
            emit_stream_event(state, "delta_reset")
        return await llm.complete(system, user, **kwargs)


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
    def _build_user_message(self, state: GraphState, ctx: Optional[AppContext] = None) -> str:
        """Return the initial user message for this agent."""
        ...

    def _tool_iteration_budget(self, ctx: AppContext) -> int:
        """Tool-calling loop budget; subclasses may source it from settings."""
        return self.max_tool_iterations

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
        user_message = self._build_user_message(state, ctx)
        history: list[tuple[ToolCall, ToolResult]] = []
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        tool_schemas = [as_function_schema(t) for t in self.tools]

        for iteration in range(self._tool_iteration_budget(ctx)):
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

    completion_timeout_seconds: Optional[float] = None

    @abstractmethod
    def _build_user_message(self, state: GraphState, ctx: Optional[AppContext] = None) -> str:
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
            completion = streamed_completion(
                ctx.llm,
                self.system_prompt,
                self._build_user_message(state, ctx),
                state,
                node=self.name,
                temperature=ctx.settings.llm_temperature,
            )
            if self.completion_timeout_seconds is None:
                text = await completion
            else:
                text = await asyncio.wait_for(completion, timeout=self.completion_timeout_seconds)
        except Exception as exc:
            return self._fallback(state, f"LLM completion failed: {exc!r}")
        return self._parse_final(text, state, ctx)
