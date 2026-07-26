"""Memory Agent: MemGPT-style retrieval — pulls long-term semantic memories,
conversation summaries and user preferences relevant to the current query."""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.state import GraphState


async def memory_agent_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    user_id = state.get("user_id", "anonymous")
    memories: list[dict[str, Any]] = []
    preferences: dict[str, Any] = {}
    if ctx.memory is not None:
        records = await ctx.memory.recall(user_id, state["query"], limit=5)
        memories = [
            {"kind": r.kind, "content": r.content, "importance": r.importance} for r in records
        ]
        preferences = await ctx.memory.get_preferences(user_id)
    return {
        "memories": memories,
        "user_preferences": preferences,
        "trace": [*state.get("trace", []), f"memory_agent: {len(memories)} memories recalled"],
    }
