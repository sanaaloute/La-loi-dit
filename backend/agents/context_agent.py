"""Context Agent.

Assembles the conversation window so context survives long conversations,
server restarts and workflow interruptions.  Uses the ``load_conversation_buffer``
tool (a thin wrapper around the memory store).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.agents.tools.base import tool
from backend.agents.tools.registry import register_tool
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.state import GraphState


class LoadConversationBufferArgs(BaseModel):
    session_id: str
    # Default window size comes from settings (context_buffer_limit); the
    # context agent always passes its own limit explicitly.
    limit: int = Field(default_factory=lambda: get_settings().context_buffer_limit)


@tool("load_conversation_buffer", "Load the recent conversation window for a session.")
async def load_conversation_buffer(ctx: Any, state: Any, args: LoadConversationBufferArgs) -> list[dict[str, Any]]:
    from backend.core.models import plain_message_content

    max_chars = ctx.settings.context_message_max_chars
    buffer: list[dict[str, Any]] = []
    if ctx.memory is not None and args.session_id:
        messages = await ctx.memory.load_buffer(args.session_id, limit=args.limit)
        buffer = [
            {"role": m.role, "content": plain_message_content(m.content)[:max_chars]}
            for m in messages
        ]
    return buffer


register_tool(load_conversation_buffer)


class ContextAgent(Agent):
    """Loads conversation context for the current session."""

    name = "context_agent"
    system_prompt = (
        "You are the context agent. Load the recent conversation history so that "
        "subsequent agents can produce coherent, multi-turn answers."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        session_id = state.get("session_id", "")
        call = ToolCall(
            name="load_conversation_buffer",
            arguments={"session_id": session_id, "limit": ctx.settings.context_max_turns},
        )
        results = await execute_tool_calls(TOOL_REGISTRY, [call], ctx, state)
        result = results[0]
        buffer = result.output if result.error is None else []
        return {
            "conversation_context": buffer,
            "trace": [*state.get("trace", []), f"context_agent: {len(buffer)} messages in window"],
            "errors": [*state.get("errors", []), f"context_agent: {result.error}"] if result.error else [],
        }


context_agent_node = ContextAgent().run
