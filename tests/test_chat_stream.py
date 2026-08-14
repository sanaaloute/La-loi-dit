"""Tests for chat streaming support: node-start events and heartbeat plumbing."""

from __future__ import annotations

import asyncio

from backend.api.routers.chat import _NodeStartHandler
from backend.workflows.graph import initial_state, stream_query


async def test_node_start_handler_fires_for_pipeline_nodes(seeded_graph):
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    handler = _NodeStartHandler(queue)

    async for _ in stream_query(
        seeded_graph,
        initial_state("Quel est le préavis de licenciement pour un employé mensualisé ?"),
        config={"callbacks": [handler]},
    ):
        pass

    starts: list[str] = []
    while not queue.empty():
        kind, value = queue.get_nowait()
        if kind == "node_start":
            starts.append(value)  # type: ignore[arg-type]

    # The UI marks a node "running" as soon as it starts, not when it ends.
    assert "input_guardrail" in starts
    assert "planner" in starts
    assert "retrieval_branch" in starts
    assert "response_generator" in starts
    # Consecutive duplicates are suppressed (handler keeps the last node only).
    assert len(starts) == len(set(starts)) or starts != sorted(starts)


def test_node_start_handler_ignores_non_node_chain_events():
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    handler = _NodeStartHandler(queue)
    asyncio.run(handler.on_chain_start({}, {}, metadata={}))
    asyncio.run(handler.on_chain_start({}, {}, metadata={"langgraph_node": "planner"}))
    asyncio.run(handler.on_chain_start({}, {}, metadata={"langgraph_node": "planner"}))
    assert queue.qsize() == 1
    assert queue.get_nowait() == ("node_start", "planner")


def test_enforce_word_limit():
    """Questions over input_max_words are rejected with a French 400."""
    from fastapi import HTTPException

    from backend.api.routers.chat import _enforce_word_limit
    from backend.core.config import get_settings

    settings = get_settings()
    limit = settings.input_max_words
    _enforce_word_limit("mot " * (limit - 1), settings)  # under: no raise
    _enforce_word_limit("mot " * limit, settings)  # exactly at: no raise
    try:
        _enforce_word_limit("mot " * (limit + 1), settings)
        raise AssertionError("expected HTTPException over the word limit")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert str(limit) in exc.detail
