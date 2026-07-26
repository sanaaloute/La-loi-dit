"""Shared helpers for agent nodes.

Every node is `async def node(state, ctx) -> dict` — it reads GraphState,
does one job, and returns the state keys it updates. Nodes never raise on
LLM/infra failure; they fall back to deterministic heuristics and append to
`errors`/`trace` so the pipeline keeps its reliability guarantees.
"""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.state import GraphState


def trace_step(state: GraphState, step: str) -> list[str]:
    return [*state.get("trace", []), step]


def append_error(state: GraphState, message: str) -> list[str]:
    return [*state.get("errors", []), message]


class AgentNode:
    """Base class for nodes implemented as classes (kept for modularity)."""

    name: str = "agent"

    async def __call__(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        raise NotImplementedError
