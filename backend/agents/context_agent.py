"""Context Agent: assembles the conversation window so context survives
long conversations, server restarts and workflow interruptions (buffer is
persisted; Temporal replays in-flight runs)."""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.models import plain_message_content
from backend.core.state import GraphState


async def context_agent_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    session_id = state.get("session_id", "")
    buffer: list[dict[str, Any]] = []
    max_turns = ctx.settings.context_max_turns
    if ctx.memory is not None and session_id:
        messages = await ctx.memory.load_buffer(session_id, limit=max_turns * 2)
        # Assistant turns are stored as FinalAnswer JSON; unwrap to the plain
        # answer text (capped like the former storage format) for prompts.
        buffer = [
            {"role": m.role, "content": plain_message_content(m.content)[:2000]}
            for m in messages
        ]
    return {
        "conversation_context": buffer,
        "trace": [*state.get("trace", []), f"context_agent: {len(buffer)} messages in window"],
    }
