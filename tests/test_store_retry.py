"""Stores must retry a failed database init instead of disabling themselves
for the process lifetime.

The one-shot bug: a single Postgres hiccup at boot permanently redirected
user data (folders, dossiers, chat history) to a fallback store — which the
next restart then abandoned, so user content "disappeared". These tests pin
the throttled retry and the strict-infra no-SQLite-fallback contract.
"""

import asyncio

import pytest

from backend.core.config import get_settings


@pytest.fixture
def unreachable_pg_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_STRICT_INFRA", "true")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/nope")
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _working_sqlite(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'healed.db').as_posix()}"


def test_user_store_retries_failed_init(unreachable_pg_settings, tmp_path, monkeypatch):
    import backend.users.service as svc

    settings = unreachable_pg_settings
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock["t"])
    store = svc.UserStore(settings)

    assert asyncio.run(store._ensure_db()) is False  # attempt 1: Postgres unreachable
    assert asyncio.run(store._ensure_db()) is False  # throttled: no second attempt

    clock["t"] += svc.UserStore._RETRY_AFTER_SECONDS + 1
    settings.database_url = _working_sqlite(tmp_path)
    assert asyncio.run(store._ensure_db()) is True  # retry past the window heals
    assert asyncio.run(store._ensure_db()) is True


def test_memory_store_retries_and_skips_sqlite_fallback_under_strict_infra(
    unreachable_pg_settings, tmp_path, monkeypatch
):
    import backend.memory.store as svc

    settings = unreachable_pg_settings
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock["t"])
    store = svc.MemoryStore(settings, None, None)

    assert asyncio.run(store._ensure_db()) is False
    assert not (tmp_path / "memory_fallback.db").exists()  # strict: no silent fallback

    clock["t"] += svc.MemoryStore._RETRY_AFTER_SECONDS + 1
    settings.database_url = _working_sqlite(tmp_path)
    assert asyncio.run(store._ensure_db()) is True


def test_memory_store_sqlite_fallback_when_not_strict(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_STRICT_INFRA", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/nope")
    get_settings.cache_clear()
    try:
        import backend.memory.store as svc

        store = svc.MemoryStore(get_settings(), None, None)
        assert asyncio.run(store._ensure_db()) is True  # SQLite fallback engaged
        assert (tmp_path / "memory_fallback.db").exists()
    finally:
        get_settings.cache_clear()


def test_knowledge_store_retries_failed_init(unreachable_pg_settings, tmp_path, monkeypatch):
    import backend.knowledge.store as svc

    settings = unreachable_pg_settings
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock["t"])
    store = svc.LegalGraphStore(settings)

    assert asyncio.run(store._ensure_db()) is False
    assert asyncio.run(store._ensure_db()) is False  # throttled

    clock["t"] += svc.LegalGraphStore._RETRY_AFTER_SECONDS + 1
    settings.database_url = _working_sqlite(tmp_path)
    assert asyncio.run(store._ensure_db()) is True
