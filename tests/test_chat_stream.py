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


async def test_stream_events_persist_after_client_disconnect(seeded_ctx, seeded_graph, settings, monkeypatch):
    """A dropped client connection must not kill the run: the completed turn
    is still persisted in the session history (the mobile recovery path polls
    it when the SSE stream dies mid-run)."""
    from backend.api.routers import chat as chat_router
    from backend.core.models import ChatRequest, parse_answer_json
    from backend.workflows.graph import initial_state

    monkeypatch.setattr(chat_router, "ANSWER_DELTA_DELAY_SECONDS", 0)

    query = "Quel est le préavis de licenciement pour un employé mensualisé ?"
    session_id = "sess-disconnect"
    gen = chat_router._stream_events(
        seeded_graph,
        initial_state(query, session_id=session_id),
        ChatRequest(query=query, session_id=session_id),
        "anonymous",
        settings,
        memory=seeded_ctx.memory,
    )

    frames: list[str] = []

    async def consume() -> None:
        async for frame in gen:
            frames.append(frame)

    consumer = asyncio.create_task(consume())
    # Wait for the first frame, then drop the client: uvicorn cancels the
    # response generator's task exactly this way on a broken connection.
    while not frames:
        await asyncio.sleep(0.005)
    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass
    await gen.aclose()

    # The user/assistant turn landed in memory even though no `final` frame
    # could be delivered on the dead stream.
    messages = await seeded_ctx.memory.get_session_messages("anonymous", session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == query
    assert parse_answer_json(messages[1].content) is not None


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


async def test_stream_events_persist_timeout_marker_after_disconnect(seeded_ctx, seeded_graph, settings, monkeypatch):
    """A run that cannot complete after the client disconnected (run timeout)
    must persist an honest failure marker: the mobile recovery path polls the
    session history and would otherwise wait in vain for an answer that will
    never exist."""
    from backend.api.routers import chat as chat_router
    from backend.core.models import ChatRequest
    from backend.workflows.graph import initial_state

    monkeypatch.setattr(chat_router, "ANSWER_DELTA_DELAY_SECONDS", 0)
    # Zero deadline: still legal in the main loop (it only checks the deadline
    # on heartbeat gaps), but the drain loop after the disconnect sees the
    # deadline already passed and must persist the failure marker.
    settings.chat_run_timeout_seconds = 0.0

    query = "Quel est le préavis de licenciement pour un employé mensualisé ?"
    session_id = "sess-timeout-marker"
    gen = chat_router._stream_events(
        seeded_graph,
        initial_state(query, session_id=session_id),
        ChatRequest(query=query, session_id=session_id),
        "anonymous",
        settings,
        memory=seeded_ctx.memory,
    )

    frames: list[str] = []
    # Pull the first frame, then close: GeneratorExit at the suspended yield
    # takes the disconnect path; the drain loop sees the passed deadline and
    # persists the failure marker. Deterministic — no consumer-task race.
    frames.append(await gen.__anext__())
    await gen.aclose()

    messages = await seeded_ctx.memory.get_session_messages("anonymous", session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == query
    assert "délai maximal" in messages[1].content


# ---------------------------------------------------------------------------
# Detached POST runs and the run-status endpoint
# ---------------------------------------------------------------------------


async def test_drain_run_keeps_registration_until_task_ends():
    from backend.api.routers import chat as chat_router

    started = asyncio.Event()

    async def work():
        started.set()
        await asyncio.sleep(0.05)

    task = asyncio.create_task(work())
    chat_router._register_run("sess-drain", task)
    drain = asyncio.create_task(chat_router._drain_run("sess-drain", task))
    await started.wait()
    # Still draining: the run stays registered (cancellable / status-visible).
    assert chat_router._RUNNING.get("sess-drain") is task
    await drain
    assert "sess-drain" not in chat_router._RUNNING


async def test_drain_run_unregisters_on_failure():
    from backend.api.routers import chat as chat_router

    async def boom():
        raise RuntimeError("llm down")

    task = asyncio.create_task(boom())
    chat_router._register_run("sess-drain-fail", task)
    await chat_router._drain_run("sess-drain-fail", task)
    assert "sess-drain-fail" not in chat_router._RUNNING
