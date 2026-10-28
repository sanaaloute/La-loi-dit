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
from backend.core.models import ChatRequest, FinalAnswer, Role
from backend.security.jwt import TokenPayload
from backend.workflows.graph import initial_state


_ANON = TokenPayload(sub="anonymous", role=Role.USER, exp=0, user_id=None)


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
            _ANON,
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
                    "citation_verification": {
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
                    _ANON,
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


def test_disconnected_stream_drains_persists_and_pushes_owner(monkeypatch):
    """Client drops mid-run (phone locked): the generator drains the run,
    persists the turn, and pushes 'answer ready' to the owner's devices only."""

    class QuickGraph:
        def astream(self, state, *, config=None, stream_mode=None):
            async def generate():
                yield {
                    "citation_verification": {
                        "final_answer": FinalAnswer(answer="Réponse prête").model_dump(mode="json")
                    }
                }

            return generate()

    class Memory:
        def __init__(self):
            self.turns = []

        async def append_turn(self, session_id, user_id, turns):
            self.turns.extend(turns)

    class UserStore:
        def __init__(self):
            self.tokens = {
                "user-1": ["ExponentPushToken[owner]"],
                "user-2": ["ExponentPushToken[stranger]"],
            }

        async def list_push_tokens_for_user(self, user_id):
            return list(self.tokens.get(user_id, []))

        async def delete_push_token_unscoped(self, token):
            pass

    async def scenario():
        import backend.core.push as push_module

        sent: list[dict] = []

        async def fake_post(messages):
            sent.extend(messages)
            return {"data": [{"status": "ok"} for _ in messages]}

        monkeypatch.setattr(push_module, "_post", fake_post)

        settings = Settings(
            llm_provider="mock",
            chat_run_timeout_seconds=5.0,
            chat_heartbeat_seconds=0.05,
            langfuse_public_key="",
            langfuse_secret_key="",
            push_notifications_enabled=True,
        )
        ctx = SimpleNamespace(
            cache=InMemoryCache(), settings=settings, user_store=UserStore()
        )
        memory = Memory()
        owner = TokenPayload(sub="user-1", role=Role.USER, exp=0, user_id="user-1")

        gen = _stream_events(
            QuickGraph(),
            initial_state("question", session_id="s-1"),
            ChatRequest(query="question", session_id="s-1"),
            owner,
            settings,
            memory=memory,
            ctx=ctx,
        )
        # One frame arrives, then the connection drops like a locked phone's
        # does: closing the generator throws GeneratorExit at the open yield.
        first = await gen.__anext__()
        assert first.startswith("data: ")
        await gen.aclose()

        # The drained run persisted the turn so the client can recover it...
        assert [message.role for message in memory.turns] == ["user", "assistant"]
        # ...and only the asking user's devices were notified.
        assert [message["to"] for message in sent] == ["ExponentPushToken[owner]"]
        assert sent[0]["data"] == {"kind": "chat_answer", "session_id": "s-1"}

    asyncio.run(scenario())
