"""Context Agent: assembles the conversation window so context survives
long conversations, server restarts and workflow interruptions (buffer is
persisted; Temporal replays in-flight runs)."""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.state import GraphState


async def context_agent_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    session_id = state.get("session_id", "")
    buffer: list[dict[str, Any]] = []
    max_turns = ctx.settings.context_max_turns
    if ctx.memory is not None and session_id:
        messages = await ctx.memory.load_buffer(session_id, limit=max_turns * 2)
        buffer = [{"role": m.role, "content": m.content} for m in messages]
    return {
        "conversation_context": buffer,
        "trace": [*state.get("trace", []), f"context_agent: {len(buffer)} messages in window"],
    }
