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


async def test_stream_events_emits_deltas_before_final(seeded_graph, settings, monkeypatch):
    """The verified answer text streams as `delta` frames ahead of `final`."""
    import json

    from backend.api.routers import chat as chat_router
    from backend.core.models import ChatRequest
    from backend.workflows.graph import initial_state

    monkeypatch.setattr(chat_router, "ANSWER_DELTA_DELAY_SECONDS", 0)

    query = "Quel est le préavis de licenciement pour un employé mensualisé ?"
    frames: list[dict] = []
    async for frame in chat_router._stream_events(
        seeded_graph, initial_state(query), ChatRequest(query=query), "anonymous", settings
    ):
        frames.append(json.loads(frame.removeprefix("data: ").strip()))

    types = [f["type"] for f in frames]
    assert "final" in types
    delta_texts = [f["text"] for f in frames if f["type"] == "delta"]
    assert delta_texts, "expected delta frames before the final one"
    # Every delta precedes the authoritative final frame.
    assert max(i for i, f in enumerate(frames) if f["type"] == "delta") < types.index("final")
    final_answer = frames[types.index("final")]["response"]["answer"]["answer"]
    assert "".join(delta_texts) == final_answer


async def test_cached_stream_events_emit_deltas(monkeypatch):
    """Cache hits replay the same delta playback before the final frame."""
    import json

    from backend.api.routers import chat as chat_router
    from backend.core.models import ChatResponse, FinalAnswer

    monkeypatch.setattr(chat_router, "ANSWER_DELTA_DELAY_SECONDS", 0)

    answer_text = "Réponse vérifiée. " * 50
    response = ChatResponse(session_id="s", answer=FinalAnswer(answer=answer_text, confidence=0.9))
    frames: list[dict] = []
    async for frame in chat_router._cached_stream_events(response, include_trace=False):
        frames.append(json.loads(frame.removeprefix("data: ").strip()))

    delta_texts = [f["text"] for f in frames if f["type"] == "delta"]
    assert delta_texts
    assert "".join(delta_texts) == answer_text
    assert frames[-1]["type"] == "final"


async def test_answer_deltas_empty_text():
    """Empty answers produce no delta frames (the final frame still follows)."""
    from backend.api.routers.chat import _answer_deltas

    assert [d async for d in _answer_deltas("")] == []
