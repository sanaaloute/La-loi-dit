import asyncio
import json
from types import SimpleNamespace

from backend.api.routers.chat import (
    _RUNNING,
    _cancel_run,
    _is_timed_out,
    _register_run,
    _stream_events,
    _unregister_run,
)
from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.models import ChatRequest, FinalAnswer
from backend.workflows.graph import initial_state


def _ctx(cache, timeout=60.0):
    settings = Settings(
        llm_provider="mock",
        chat_run_timeout_seconds=timeout,
        chat_heartbeat_seconds=0.05,
        langfuse_public_key="",
        langfuse_secret_key="",
    )
    return SimpleNamespace(cache=cache, settings=settings)


def test_cancel_reaches_task_registered_on_another_worker():
    cache = InMemoryCache()
    owner = _ctx(cache)
    other_worker = _ctx(cache)
    session_id = "shared-session"
    task: asyncio.Task

    async def scenario():
        nonlocal task
        task = asyncio.create_task(asyncio.sleep(60))
        await _register_run(session_id, task, owner, "run-1")
        # Simulate the cancel request arriving on a worker without the local task.
        _RUNNING.pop(session_id, None)
        assert await _cancel_run(session_id, other_worker) is True
        await asyncio.sleep(1.2)
        assert task.cancelled()
        await _unregister_run(session_id, task, owner)

    try:
        asyncio.run(scenario())
    finally:
        task.cancel()
        _RUNNING.clear()


def test_watchdog_enforces_run_timeout():
    cache = InMemoryCache()
    ctx = _ctx(cache, timeout=0.2)
    session_id = "timeout-session"
    task: asyncio.Task

    async def scenario():
        nonlocal task
        task = asyncio.create_task(asyncio.sleep(60))
        await _register_run(session_id, task, ctx, "run-timeout")
        await asyncio.sleep(0.6)
        assert task.cancelled()
        assert _is_timed_out(task)
        await _unregister_run(session_id, task, ctx)

    try:
        asyncio.run(scenario())
    finally:
        task.cancel()
        _RUNNING.clear()


def test_stream_timeout_is_an_error_not_a_user_cancellation():
    class SlowGraph:
        def astream(self, state, *, config=None, stream_mode=None):
            async def generate():
                await asyncio.sleep(10)
                yield {}

            return generate()

    async def scenario():
        ctx = _ctx(InMemoryCache(), timeout=0.2)
        frames = []
        async for frame in _stream_events(
            SlowGraph(),
            initial_state("slow"),
            ChatRequest(query="slow"),
            "anonymous",
            ctx.settings,
            ctx=ctx,
        ):
            frames.append(frame)
        assert frames
        assert '"type": "error"' in frames[-1]
        assert '"type": "cancelled"' not in frames[-1]

    asyncio.run(scenario())


def test_terminal_update_emits_final_without_waiting_for_stream_cleanup():
    class TerminalThenSlowCleanupGraph:
        def astream(self, state, *, config=None, stream_mode=None):
            async def generate():
                yield {
                    "output_guardrail": {
                        "final_answer": FinalAnswer(answer="Réponse vérifiée").model_dump(mode="json")
                    }
                }
                await asyncio.sleep(60)
                yield {}

            return generate()

    class Memory:
        def __init__(self):
            self.turns = []

        async def append_turn(self, session_id, user_id, turns):
            self.turns.extend(turns)

        async def load_buffer(self, session_id, limit):
            return self.turns

    async def scenario():
        ctx = _ctx(InMemoryCache(), timeout=5.0)
        memory = Memory()

        async def collect():
            return [
                frame
                async for frame in _stream_events(
                    TerminalThenSlowCleanupGraph(),
                    initial_state("terminal", session_id="terminal-session"),
                    ChatRequest(query="terminal", session_id="terminal-session"),
                    "anonymous",
                    ctx.settings,
                    memory=memory,
                    ctx=ctx,
                )
            ]

        frames = await asyncio.wait_for(collect(), timeout=1.0)
        events = [
            json.loads(frame.removeprefix("data: "))
            for frame in frames
            if frame.startswith("data: ")
        ]
        assert events[-1]["type"] == "final"
        assert events[-1]["response"]["answer"]["answer"] == "Réponse vérifiée"
        assert not any(event["type"] == "cancelled" for event in events)
        assert [message.role for message in memory.turns] == ["user", "assistant"]

    asyncio.run(scenario())
