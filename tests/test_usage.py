"""Phase 4 tests: token metering, daily quotas, answer cache, cheap routing.

Fully offline (mock LLM, tmp SQLite). Each test builds a fresh app with a
tmp database; cache tests additionally relax the confidence knobs so the
seeded mock answers are cache-eligible.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core import catalog  # noqa: E402
from backend.core.config import Settings, get_settings  # noqa: E402
from backend.core.exceptions import AuthorizationError  # noqa: E402
from backend.core.model_router import is_simple_query, resolve_llm  # noqa: E402
from backend.core.models import Role  # noqa: E402
from backend.security.jwt import TokenPayload  # noqa: E402

PASSWORD = "motdepasse1"


@contextmanager
def _make_client(tmp_path, monkeypatch, extra_env: dict | None = None):
    """Fresh app + TestClient with a tmp database and custom env."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/usage_test.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch) as test_client:
        yield test_client


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


def _chat(client, query: str, token: str | None = None, **extra) -> dict:
    headers = _headers(token) if token else {}
    response = client.post("/api/v1/chat", json={"query": query, **extra}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _seed_evidence(client) -> None:
    """Seed the app's vector store so answers carry real confidence."""
    from backend.evaluation.seed_data import seed_evidence

    ctx = client.app.state.ctx
    chunks = seed_evidence()
    vectors = asyncio.run(ctx.embedder.embed([c.content for c in chunks]))
    asyncio.run(ctx.vector_store.upsert(chunks, vectors))


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------


def test_chat_meters_usage_for_db_user(client):
    _, token = _register(client)
    _chat(client, "Quel est le préavis de licenciement au Burkina Faso ?", token)

    usage = client.get("/api/v1/usage/me", headers=_headers(token)).json()
    assert usage["today"]["requests"] == 1
    assert usage["today"]["tokens_in"] > 0
    first_total = usage["today"]["tokens_in"] + usage["today"]["tokens_out"]
    assert first_total > 0

    # Different query (identical one would be served by the answer cache).
    _chat(client, "Quels sont les droits du salarié en cas de licenciement ?", token)
    usage = client.get("/api/v1/usage/me", headers=_headers(token)).json()
    assert usage["today"]["requests"] == 2
    assert usage["today"]["tokens_in"] + usage["today"]["tokens_out"] > first_total
    assert len(usage["history"]) == 1


def test_anonymous_chat_is_not_metered(client):
    _chat(client, "Quel est le préavis de licenciement ?")
    # No account at all: nothing recorded anywhere, endpoint stays 401.
    assert client.get("/api/v1/usage/me").status_code == 401


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


def _tiny_budget_catalog() -> str:
    override = json.loads(json.dumps(catalog.TIER_CATALOG))  # deep copy
    for tier in override.values():
        tier["daily_token_budget"] = 10
    return json.dumps(override)


def test_budget_exceeded_returns_429(tmp_path, monkeypatch):
    with _make_client(
        tmp_path,
        monkeypatch,
        {"LEGAL_AI_TIER_CATALOG_JSON": _tiny_budget_catalog()},
    ) as client:
        _, token = _register(client)
        # First chat fits (today = 0); the mock metering blows past 10 tokens.
        _chat(client, "Quel est le préavis de licenciement au Burkina Faso ?", token)

        response = client.post(
            "/api/v1/chat",
            json={"query": "Autre question juridique ?"},
            headers=_headers(token),
        )
        assert response.status_code == 429
        assert "Quota journalier de tokens atteint" in response.json()["detail"]

        # Drafting is blocked too (pro tier needed for the feature itself).
        me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
        asyncio.run(client.app.state.ctx.user_store.set_tier(me["id"], "pro"))
        response = client.post(
            "/api/v1/draft",
            json={
                "template_id": "contrat_travail_cdi",
                "fields": {
                    "employeur": "Faso SARL",
                    "salarie": "Awa",
                    "poste": "Comptable",
                    "date_debut": "2026-08-01",
                    "salaire": "100000",
                },
            },
            headers=_headers(token),
        )
        assert response.status_code == 429

        # Anonymous users are not metered: unaffected by quotas.
        response = client.post("/api/v1/chat", json={"query": "Le préavis au Burkina ?"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Answer cache
# ---------------------------------------------------------------------------

_CACHE_ENV = {
    "LEGAL_AI_CONFIDENCE_THRESHOLD": "0.1",
    "LEGAL_AI_HUMAN_REVIEW_THRESHOLD": "0.0",
}


def test_same_query_twice_hits_cache(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, _CACHE_ENV) as client:
        _seed_evidence(client)
        query = "Quel est le préavis de licenciement au Burkina Faso ?"
        first = _chat(client, query)
        assert first["answer"]["metadata"].get("cache_hit") is not True

        second = _chat(client, query)
        assert second["answer"]["metadata"].get("cache_hit") is True
        assert second["answer"]["answer"] == first["answer"]["answer"]


def test_refusal_is_not_cached(tmp_path, monkeypatch):
    # Force a hard refusal in the output-guard so we can verify that refused
    # answers are never written to the answer cache, even when the same query
    # is repeated.
    import backend.guardrails.output_guard as og

    original_check_output = og.check_output

    async def _force_refuse(answer, evidence, settings):
        answer = await original_check_output(answer, evidence, settings)
        if not evidence:
            answer.refused = True
            answer.refusal_reason = "Forced refusal for cache test"
        return answer

    monkeypatch.setattr(og, "check_output", _force_refuse)

    with _make_client(tmp_path, monkeypatch, _CACHE_ENV) as client:
        query = "What is the airspeed velocity of an unladen swallow?"
        first = _chat(client, query)
        print("FIRST ANSWER:", first["answer"])
        assert first["answer"]["refused"] is True
        second = _chat(client, query)
        assert second["answer"]["refused"] is True
        assert second["answer"]["metadata"].get("cache_hit") is not True


def test_low_confidence_is_not_cached(tmp_path, monkeypatch):
    # No evidence seeded -> confidence 0 -> below threshold -> never cached.
    with _make_client(tmp_path, monkeypatch, _CACHE_ENV) as client:
        query = "Question sur un point de droit totalement absent du corpus."
        first = _chat(client, query)
        assert first["answer"]["metadata"].get("cache_hit") is not True
        second = _chat(client, query)
        assert second["answer"]["metadata"].get("cache_hit") is not True


def test_different_model_separate_cache_entries(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, _CACHE_ENV) as client:
        _seed_evidence(client)
        query = "Quel est le préavis de licenciement ?"
        _chat(client, query, model="ollama/gpt-oss:20b")
        # A different model must not hit the first entry.
        other = _chat(client, query, model="ollama/gemma4:31b")
        assert other["answer"]["metadata"].get("cache_hit") is not True
        # Repeating the same model does hit.
        repeat = _chat(client, query, model="ollama/gemma4:31b")
        assert repeat["answer"]["metadata"].get("cache_hit") is True


def test_session_context_bypasses_cache(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, _CACHE_ENV) as client:
        _seed_evidence(client)
        query = "Quel est le préavis de licenciement au Burkina Faso ?"
        _chat(client, query)  # populates the cache
        # Same query mid-conversation (explicit session_id) must bypass.
        follow_up = _chat(client, query, session_id=uuid.uuid4().hex)
        assert follow_up["answer"]["metadata"].get("cache_hit") is not True


# ---------------------------------------------------------------------------
# Usage endpoint
# ---------------------------------------------------------------------------


def test_usage_me_shape_and_remaining_math(client):
    _, token = _register(client)
    usage = client.get("/api/v1/usage/me", headers=_headers(token)).json()
    assert usage["tier"] == "gratuit"
    assert usage["daily_budget"] == 100_000_000  # dev mode: effectively unlimited
    assert usage["today"] == {"tokens_in": 0, "tokens_out": 0, "requests": 0}
    assert usage["remaining_tokens"] == 100_000_000
    assert usage["history"] == []

    _chat(client, "Quel est le préavis de licenciement au Burkina Faso ?", token)
    usage = client.get("/api/v1/usage/me", headers=_headers(token)).json()
    consumed = usage["today"]["tokens_in"] + usage["today"]["tokens_out"]
    assert usage["remaining_tokens"] == 100_000_000 - consumed
    assert usage["history"][0]["requests"] == 1


# ---------------------------------------------------------------------------
# Cheap-model routing
# ---------------------------------------------------------------------------


def _ctx(settings: Settings):
    return SimpleNamespace(settings=settings, llm=None)


def _user(tier: str) -> TokenPayload:
    return TokenPayload(sub="u1", role=Role.USER, exp=9_999_999_999, user_id="u1", tier=tier)


def test_simple_query_routes_to_cheapest_model():
    ctx = _ctx(Settings(llm_provider="openai"))
    client = resolve_llm(ctx, _user("gratuit"), query="Préavis licenciement ?")
    assert client.model == "ollama/gpt-oss:20b"  # first (cheapest) gratuit entry


def test_complex_query_routes_to_tier_default():
    ctx = _ctx(Settings(llm_provider="openai"))
    complex_query = "Explique et analyse les clauses du contrat de travail en détail."
    client = resolve_llm(ctx, _user("gratuit"), query=complex_query)
    # Gratuit default is the mid OpenRouter model.
    assert client.model == "openrouter/meta-llama/llama-3.3-70b-instruct"


def test_explicit_model_always_wins():
    ctx = _ctx(Settings(llm_provider="openai"))
    client = resolve_llm(ctx, _user("gratuit"), "ollama/gemma4:31b", query="Préavis ?")
    assert client.model == "ollama/gemma4:31b"
    with pytest.raises(AuthorizationError):
        resolve_llm(ctx, _user("gratuit"), "openrouter/openai/gpt-99", query="Préavis ?")


def test_is_simple_query_heuristic():
    assert is_simple_query("Préavis licenciement ?")
    assert not is_simple_query("Explique le contrat de travail")
    assert not is_simple_query("x" * 200)
    assert not is_simple_query("ligne une\nligne deux")
