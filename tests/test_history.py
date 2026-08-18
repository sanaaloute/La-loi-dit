"""Phase 2 tests: chat history API, ownership scoping, workspace_name.

Fully offline (mock LLM, tmp SQLite). Fresh app per test with a tmp database
so accounts and history never leak into the dev database.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core.config import get_settings  # noqa: E402

PASSWORD = "motdepasse1"
QUERY = "Quel est le préavis de licenciement au Burkina Faso ?"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app + TestClient with a tmp user database (offline)."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/history_test.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _register(client, name: str = "Awa") -> tuple[str, str]:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": name},
    )
    assert response.status_code == 201, response.text
    return email, response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _chat(client, token: str, session_id: str | None = None) -> dict:
    payload: dict = {"query": QUERY}
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/api/v1/chat", json=payload, headers=_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# History recording & listing
# ---------------------------------------------------------------------------


def test_logged_in_chat_appears_in_history(client):
    _, token = _register(client)
    chat = _chat(client, token)
    session_id = chat["session_id"]

    response = client.get("/api/v1/chat/sessions", headers=_headers(token))
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    entry = sessions[0]
    assert entry["session_id"] == session_id
    assert entry["title"].startswith("Quel est le préavis")
    assert len(entry["title"]) <= 80
    assert entry["message_count"] == 2
    assert entry["created_at"] and entry["updated_at"]

    # A second turn in the same session grows the count.
    _chat(client, token, session_id=session_id)
    sessions = client.get("/api/v1/chat/sessions", headers=_headers(token)).json()["sessions"]
    assert sessions[0]["message_count"] == 4


def test_session_detail_returns_turns_with_parsed_answer(client):
    _, token = _register(client)
    session_id = _chat(client, token)["session_id"]

    response = client.get(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token))
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    messages = data["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]

    user_turn, assistant_turn = messages
    assert user_turn["answer"] is None
    assert QUERY in user_turn["content"]
    assert user_turn["created_at"]

    assert assistant_turn["answer"] is not None
    assert assistant_turn["answer"]["answer"]  # FinalAnswer payload parsed back
    assert "confidence" in assistant_turn["answer"]


def test_turns_recorded_under_db_user_id(client):
    """History is keyed by the stable DB user id, not the email/subject."""
    _, token = _register(client)
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    _chat(client, token)

    memory = client.app.state.ctx.memory
    by_db_id = asyncio.run(memory.list_sessions(me["id"]))
    by_email = asyncio.run(memory.list_sessions(me["email"]))
    assert len(by_db_id) == 1
    assert by_email == []


# ---------------------------------------------------------------------------
# Ownership scoping & auth requirements
# ---------------------------------------------------------------------------


def test_user_b_cannot_read_user_a_session(client):
    _, token_a = _register(client, name="Awa")
    session_id = _chat(client, token_a)["session_id"]

    _, token_b = _register(client, name="Boureima")
    sessions = client.get("/api/v1/chat/sessions", headers=_headers(token_b)).json()["sessions"]
    assert sessions == []

    response = client.get(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token_b))
    assert response.status_code == 404


def test_unknown_session_returns_404(client):
    _, token = _register(client)
    response = client.get("/api/v1/chat/sessions/does-not-exist", headers=_headers(token))
    assert response.status_code == 404


def test_history_endpoints_require_auth(client):
    assert client.get("/api/v1/chat/sessions").status_code == 401
    assert client.get("/api/v1/chat/sessions/whatever").status_code == 401


# ---------------------------------------------------------------------------
# /auth/me workspace_name + unchanged anonymous chat
# ---------------------------------------------------------------------------


def test_me_includes_workspace_name(client):
    _, token = _register(client, name="Awa")
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert me["workspace_name"] == "Espace Awa"
    assert me["workspace_id"]


def test_anonymous_chat_still_works(client):
    response = client.post("/api/v1/chat", json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["answer"]["answer"]


# ---------------------------------------------------------------------------
# SSE-streamed chats persist their turns like POST /chat
# ---------------------------------------------------------------------------


def test_streamed_chat_is_persisted_in_history(client):
    _, token = _register(client)
    session_id = uuid.uuid4().hex

    with client.stream(
        "GET",
        "/api/v1/chat/stream",
        params={"query": QUERY, "session_id": session_id},
        headers=_headers(token),
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert '"type": "final"' in body  # stream completed with a final answer

    sessions = client.get("/api/v1/chat/sessions", headers=_headers(token)).json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["message_count"] == 2

    detail = client.get(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token)).json()
    messages = detail["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == QUERY
    assert messages[1]["answer"] is not None
    assert messages[1]["answer"]["answer"]


def test_streamed_chat_scoped_to_caller(client):
    _, token_a = _register(client, name="Awa")
    session_id = uuid.uuid4().hex
    client.get(
        "/api/v1/chat/stream",
        params={"query": QUERY, "session_id": session_id},
        headers=_headers(token_a),
    )

    _, token_b = _register(client, name="Boureima")
    response = client.get(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token_b))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Session deletion
# ---------------------------------------------------------------------------


def test_delete_session_removes_it(client):
    _, token = _register(client)
    session_id = _chat(client, token)["session_id"]

    response = client.delete(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token))
    assert response.status_code == 204

    sessions = client.get("/api/v1/chat/sessions", headers=_headers(token)).json()["sessions"]
    assert sessions == []
    assert client.get(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token)).status_code == 404


def test_delete_session_owner_scoped(client):
    _, token_a = _register(client, name="Awa")
    session_id = _chat(client, token_a)["session_id"]

    _, token_b = _register(client, name="Boureima")
    response = client.delete(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token_b))
    assert response.status_code == 404  # no existence leak

    # owner session untouched
    sessions = client.get("/api/v1/chat/sessions", headers=_headers(token_a)).json()["sessions"]
    assert len(sessions) == 1


def test_delete_session_requires_auth(client):
    assert client.delete("/api/v1/chat/sessions/whatever").status_code == 401


def test_session_detail_messages_have_sequential_index(client):
    """Each prompt and its answer carry a simple per-session index (0, 1, 2…)
    so clients can match them without comparing text."""
    _, token = _register(client)
    session_id = _chat(client, token, session_id="sess-index")["session_id"]

    response = client.get(f"/api/v1/chat/sessions/{session_id}", headers=_headers(token))
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [m["index"] for m in messages] == list(range(len(messages)))


def test_run_status_endpoint_reflects_in_flight_runs(client):
    """The recovery path polls this to tell 'still computing' from 'dead'."""
    from backend.api.routers.chat import _RUNNING

    class _FakeTask:
        def __init__(self, done: bool) -> None:
            self._done = done

        def done(self) -> bool:
            return self._done

    _, token = _register(client)
    sid = "sess-run-status"
    url = f"/api/v1/chat/sessions/{sid}/run"

    response = client.get(url, headers=_headers(token))
    assert response.status_code == 200
    assert response.json() == {"running": False}

    _RUNNING[sid] = _FakeTask(done=False)
    try:
        assert client.get(url, headers=_headers(token)).json() == {"running": True}
        _RUNNING[sid] = _FakeTask(done=True)
        assert client.get(url, headers=_headers(token)).json() == {"running": False}
    finally:
        _RUNNING.pop(sid, None)


def test_run_status_requires_auth(client):
    assert client.get("/api/v1/chat/sessions/whatever/run").status_code in (401, 403)
