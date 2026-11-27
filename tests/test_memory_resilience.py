"""Chat-turn persistence must survive transient database failures.

Incident: a drained chat run (phone locked mid-stream) hit the run deadline;
the failure-note persist failed on a stale pooled connection and was
swallowed silently, so the turn vanished — the recovering client polled the
session history in vain and the question "disappeared". These tests pin the
fixes: pool pre-ping on the store engines, time-bounded DB operations, loud
failure reporting, the in-memory mirror fallback, and the drain's persist
retry.
"""

import asyncio

import pytest

from backend.core.config import get_settings
from backend.core.models import ChatMessage


@pytest.fixture
def strict_unreachable_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_STRICT_INFRA", "true")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/nope")
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_append_turn_reports_failure_and_mirror_serves_the_turn(strict_unreachable_settings):
    from backend.memory.store import MemoryStore

    store = MemoryStore(strict_unreachable_settings, None, None)
    turn = [ChatMessage(role="user", content="question ?")]
    assert asyncio.run(store.append_turn("s1", "u1", turn)) is False
    # The mirror answers session reads while the DB is down (recovery path).
    messages = asyncio.run(store.get_session_messages("u1", "s1"))
    assert [m.content for m in messages] == ["question ?"]


def test_append_turn_mirror_is_idempotent_across_retries(strict_unreachable_settings):
    from backend.memory.store import MemoryStore

    store = MemoryStore(strict_unreachable_settings, None, None)
    turn = [ChatMessage(role="user", content="même question")]
    assert asyncio.run(store.append_turn("s1", "u1", turn)) is False
    assert asyncio.run(store.append_turn("s1", "u1", turn)) is False  # drain retry
    messages = asyncio.run(store.get_session_messages("u1", "s1"))
    assert len(messages) == 1  # a retry must not duplicate the mirrored turn


def test_memory_engine_enables_pool_pre_ping(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_STRICT_INFRA", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "LEGAL_AI_DATABASE_URL",
        f"sqlite+aiosqlite:///{(tmp_path / 'ok.db').as_posix()}",
    )
    get_settings.cache_clear()
    try:
        import sqlalchemy.ext.asyncio as sa_async

        from backend.memory.store import MemoryStore

        captured: dict = {}
        real_create = sa_async.create_async_engine

        def spy(url, **kwargs):
            captured.update(kwargs)
            return real_create(url, **kwargs)

        monkeypatch.setattr(sa_async, "create_async_engine", spy)
        store = MemoryStore(get_settings(), None, None)
        assert asyncio.run(store._ensure_db()) is True
        assert captured.get("pool_pre_ping") is True
        assert captured.get("pool_recycle") == 1800
    finally:
        get_settings.cache_clear()


def test_persist_turn_with_retry_recovers_after_transient_failure():
    from backend.api.routers.chat import _persist_turn_with_retry

    class FlakyMemory:
        def __init__(self):
            self.calls = 0

        async def append_turn(self, session_id, user_id, messages):
            self.calls += 1
            return self.calls > 1  # first attempt fails, second persists

    memory = FlakyMemory()
    ok = asyncio.run(
        _persist_turn_with_retry(
            memory, "s1", "u1", [ChatMessage(role="user", content="q")], delay_seconds=0
        )
    )
    assert ok is True
    assert memory.calls == 2


def test_persist_turn_with_retry_gives_up_without_raising():
    from backend.api.routers.chat import _persist_turn_with_retry

    class DeadMemory:
        def __init__(self):
            self.calls = 0

        async def append_turn(self, session_id, user_id, messages):
            self.calls += 1
            return False

    memory = DeadMemory()
    ok = asyncio.run(
        _persist_turn_with_retry(
            memory, "s1", "u1", [ChatMessage(role="user", content="q")], delay_seconds=0
        )
    )
    assert ok is False
    assert memory.calls == 3
