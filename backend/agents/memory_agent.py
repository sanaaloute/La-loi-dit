"""Memory Agent.

MemGPT-style retrieval — pulls long-term semantic memories, conversation
summaries and user preferences relevant to the current query.  Uses
``recall_memories`` and ``get_user_preferences`` tools.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.agents.tools.base import tool
from backend.agents.tools.registry import register_tool
from backend.core.context import AppContext
from backend.core.state import GraphState


class RecallMemoriesArgs(BaseModel):
    user_id: str
    query: str
    limit: int = 5


class GetUserPreferencesArgs(BaseModel):
    user_id: str


@tool("recall_memories", "Recall long-term semantic memories relevant to a query.")
async def recall_memories(ctx: Any, state: Any, args: RecallMemoriesArgs) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    if ctx.memory is not None:
        records = await ctx.memory.recall(args.user_id, args.query, limit=args.limit)
        memories = [
            {"kind": r.kind, "content": r.content, "importance": r.importance}
            for r in records
        ]
    return memories


@tool("get_user_preferences", "Return stored preferences for a user.")
async def get_user_preferences(ctx: Any, state: Any, args: GetUserPreferencesArgs) -> dict[str, Any]:
    if ctx.memory is not None:
        return await ctx.memory.get_preferences(args.user_id)
    return {}


register_tool(recall_memories)
register_tool(get_user_preferences)


class MemoryAgent(Agent):
    """Recalls long-term memories and user preferences."""

    name = "memory_agent"
    system_prompt = (
        "You are the memory agent. Recall facts, summaries and preferences that help "
        "answer the current legal question."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        user_id = state.get("user_id", "anonymous")
        query = state["query"]
        calls = [
            ToolCall(name="recall_memories", arguments={"user_id": user_id, "query": query, "limit": 5}),
            ToolCall(name="get_user_preferences", arguments={"user_id": user_id}),
        ]
        results = await execute_tool_calls(TOOL_REGISTRY, calls, ctx, state)
        memories = results[0].output if not results[0].error else []
        preferences = results[1].output if not results[1].error else {}
        return {
            "memories": memories,
            "user_preferences": preferences,
            "trace": [*state.get("trace", []), f"memory_agent: {len(memories)} memories recalled"],
            "errors": [
                *state.get("errors", []),
                *[f"memory_agent: {r.error}" for r in results if r.error],
            ],
        }


memory_agent_node = MemoryAgent().run
