import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.routers.chat import _direct_fast_path
from backend.core.config import get_settings
from backend.core.models import ChatRequest
from backend.security.jwt import TokenPayload
from backend.workflows.graph import initial_state


@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    """A TestClient with a fresh in-memory app and one dev user."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_REDIS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_MILVUS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    monkeypatch.setenv("LEGAL_AI_DEV_USERS", "admin:test:admin")
    monkeypatch.setenv(
        "LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/legal_ai.db"
    )
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        yield client


def _token(client: TestClient) -> str:
    res = client.post("/api/v1/auth/token", json={"username": "admin", "password": "test"})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def test_post_direct_shortcut_bypasses_graph_nodes(chat_client):
    res = chat_client.post(
        "/api/v1/chat",
        json={"query": "bonjour"},
        headers={"Authorization": f"Bearer {_token(chat_client)}"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["answer"]["answer"]
    trace = [t for t in data.get("trace", [])]
    # The fast path should never run language_gate, query_router, or output_guardrail.
    assert not any("language_gate" in t for t in trace)
    assert not any("query_router" in t for t in trace)
    assert not any("output_guardrail" in t for t in trace)
    assert any("direct_fast_path" in t for t in trace)


def test_non_shortcut_query_does_not_use_fast_path(chat_client):
    """The fast-path helper should decline to handle real legal questions."""
    ctx = chat_client.app.state.ctx
    payload = ChatRequest(query="quels sont mes droits ?")
    state = initial_state(payload.query)
    user = TokenPayload(sub="admin", role="admin", exp=0, user_id="admin")
    result = asyncio.run(
        _direct_fast_path(ctx, state, payload, user, usage_before=None, answer_cache=None, model_id="")
    )
    assert result is None


def test_stream_direct_shortcut_returns_final_frame(chat_client):
    res = chat_client.get(
        "/api/v1/chat/stream?query=hi",
        headers={"Authorization": f"Bearer {_token(chat_client)}"},
    )
    assert res.status_code == 200, res.text
    assert "text/event-stream" in res.headers.get("content-type", "")
    body = res.text
    assert '"type": "final"' in body
    assert '"node": "direct_fast_path"' in body
