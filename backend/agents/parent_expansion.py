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
    can see exactly which evidence led to each parent, and stamps the dual
    ``retrieval_text`` / ``context_text`` fields (spec §7) on expanded parents.
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
        # Dual text (spec §7): record what matched (the child passage) and
        # the broader context it was expanded to (the parent article /
        # section).  First matching child wins when several children share
        # one parent; every child stays visible under ``parent.child_chunks``.
        if parent.retrieval_text is None:
            parent.retrieval_text = child.content
        if parent.context_text is None:
            parent.context_text = parent.content
        if parent not in expanded:
            expanded.append(parent)

    # Score propagation: expanded parents never went through vector search or
    # reranking themselves, so their raw scores read 0.00 in the UI although
    # they are displayed BECAUSE a child scored well.  Surface the best
    # attached child's scores (never lowering the parent's own) and mark the
    # expansion origin so the UI can badge these as context, not noise.
    for parent in expanded:
        if not parent.child_chunks:
            continue
        parent.retrieval_score = max(
            [parent.retrieval_score] + [c.retrieval_score for c in parent.child_chunks]
        )
        parent.rerank_score = max(
            [parent.rerank_score] + [c.rerank_score for c in parent.child_chunks]
        )
        parent.confidence = max(
            [parent.confidence] + [c.confidence for c in parent.child_chunks]
        )
        parent.metadata = {**parent.metadata, "expansion": "parent"}

    return {
        "evidence": expanded,
        "trace": [
            *state.get("trace", []),
            f"parent_expansion: {len(parents)} parents expanded from {len(evidence)} children",
        ],
    }
