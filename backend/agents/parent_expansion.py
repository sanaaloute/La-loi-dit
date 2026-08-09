"""Parent Expansion Agent.

Retrieval returns small child chunks.  This node expands each top child to its
parent chunk (the article or section it belongs to) so the downstream LLM has
full context while citations still point to the original retrieved evidence.
"""

from __future__ import annotations

from typing import Any

from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.core.context import AppContext
from backend.core.state import GraphState


async def parent_expansion_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    """Replace retrieved child chunks with their parent chunks.

    The upstream retrieval/conflict-resolution steps operate on ``evidence``,
    so this node reads ``evidence`` and writes back expanded ``evidence``.
    Keeps child chunks that have no parent as-is.  Populates
    ``parent.child_chunks`` with the matching children so the response generator
    can see exactly which evidence led to each parent.
    """
    evidence = list(state.get("evidence", []))
    if not evidence or ctx.vector_store is None:
        return {
            "evidence": evidence,
            "trace": [*state.get("trace", []), "parent_expansion: no evidence"],
        }

    parent_ids = {c.parent_chunk_id for c in evidence if c.parent_chunk_id}
    if not parent_ids:
        return {
            "evidence": evidence,
            "trace": [*state.get("trace", []), "parent_expansion: no parents to expand"],
        }

    call = ToolCall(name="fetch_parent_chunks", arguments={"parent_chunk_ids": list(parent_ids)})
    results = await execute_tool_calls(TOOL_REGISTRY, [call], ctx, state)
    result = results[0]
    if result.error:
        return {
            "evidence": evidence,
            "errors": [*state.get("errors", []), f"parent_expansion: {result.error}"],
            "trace": [*state.get("trace", []), "parent_expansion: fetch failed"],
        }

    parents: list[Any] = list(result.output or [])
    parent_by_id = {p.chunk_id: p for p in parents}

    expanded: list[Any] = []
    for child in evidence:
        parent = parent_by_id.get(child.parent_chunk_id) if child.parent_chunk_id else None
        if parent is None:
            expanded.append(child)
            continue
        # Attach the child to its parent, but avoid duplicates.
        if child not in parent.child_chunks:
            parent.child_chunks.append(child)
        if parent not in expanded:
            expanded.append(parent)

    return {
        "evidence": expanded,
        "trace": [
            *state.get("trace", []),
            f"parent_expansion: {len(parents)} parents expanded from {len(evidence)} children",
        ],
    }
