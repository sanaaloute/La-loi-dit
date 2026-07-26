"""Parallel Retrieval Coordinator node: launches all retrieval workers
simultaneously (asyncio) through the retrieval subsystem, then merges and
deduplicates the results."""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.state import GraphState


async def retrieval_coordinator_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    tasks = list(state.get("tasks", []))
    existing = list(state.get("evidence", []))
    errors = list(state.get("errors", []))
    new_chunks: list[Any] = []
    if ctx.retriever is not None and tasks:
        try:
            new_chunks = await ctx.retriever.retrieve(tasks)
        except Exception as exc:
            errors.append(f"retrieval_error: {exc}")
    merged = {c.chunk_id: c for c in [*existing, *new_chunks]}
    retries = state.get("retrieval_retries", 0)
    if state.get("needs_more_retrieval") or (state.get("reflection") and state["reflection"].should_retry_retrieval):
        retries += 1
    return {
        "evidence": list(merged.values()),
        "retrieval_retries": retries,
        "needs_more_retrieval": False,
        "errors": errors,
        "trace": [
            *state.get("trace", []),
            f"retrieval_coordinator: {len(tasks)} tasks -> {len(new_chunks)} chunks "
            f"({len(merged)} total after merge)",
        ],
    }
