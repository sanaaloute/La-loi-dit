import asyncio
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
from backend.core.models import ChatRequest
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
