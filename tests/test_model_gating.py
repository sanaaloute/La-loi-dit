"""Phase 1 tests: tier catalog, model gating, registration, markdown export.

Fully offline (mock LLM, tmp SQLite). API tests build a fresh app per test
with a tmp database so accounts never leak into the dev database.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core import catalog  # noqa: E402
from backend.core.config import Settings, get_settings  # noqa: E402
from backend.core.exceptions import UserAlreadyExistsError  # noqa: E402
from backend.core.llm import LLMClient  # noqa: E402
from backend.core.model_router import resolve_llm  # noqa: E402
from backend.core.models import Role  # noqa: E402
from backend.security.jwt import TokenPayload, create_access_token  # noqa: E402

PASSWORD = "motdepasse1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app + TestClient with a tmp user database (offline)."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/users_test.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _register(client, name: str = "Awa") -> tuple[str, str]:
    """Register a fresh account; return (email, access_token)."""
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": name},
    )
    assert response.status_code == 201, response.text
    return email, response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_full_access_for_legacy_tiers():
    gratuit = {m.id for m in catalog.allowed_models("gratuit")}
    pro = {m.id for m in catalog.allowed_models("pro")}
    cabinet = {m.id for m in catalog.allowed_models("cabinet")}
    assert gratuit == pro == cabinet
    assert gratuit == {"ollama/gemma4:12b", "ollama/nemotron-3-nano:4b"}


def test_is_model_allowed_for_every_legacy_tier():
    for tier in catalog.TIER_ORDER:
        assert catalog.is_model_allowed(tier, "ollama/gemma4:12b")
        assert catalog.is_model_allowed(tier, "ollama/nemotron-3-nano:4b")
        assert not catalog.is_model_allowed(tier, "ollama/gpt-oss:20b")
        assert not catalog.is_model_allowed(tier, "tokenfree/gemini-2.5-flash")


def test_default_model_and_unknown_tier():
    assert catalog.default_model("gratuit") == "ollama/gemma4:12b"
    assert catalog.default_model("pro") == "ollama/gemma4:12b"
    assert catalog.get_tier("inconnu") == catalog.get_tier("gratuit")


def test_all_models_with_access_annotations():
    annotated = {m["id"]: m for m in catalog.all_models_with_access("pro")}
    for model_id, model in annotated.items():
        assert model["allowed"] is True, model_id
        assert model["tier_required"] == "gratuit"
    assert "ollama/nemotron-3-nano:4b" in annotated


def test_catalog_env_override_is_ignored(monkeypatch):
    override = {
        "gratuit": {
            "providers": ["mock"],
            "models": [{"id": "mock/gratuit-model", "provider": "mock"}],
            "features": {},
            "daily_token_budget": 1,
        }
    }
    monkeypatch.setenv("LEGAL_AI_TIER_CATALOG_JSON", json.dumps(override))
    get_settings.cache_clear()
    try:
        assert catalog.default_model("gratuit") == "ollama/gemma4:12b"
        assert not catalog.is_model_allowed("gratuit", "mock/gratuit-model")
    finally:
        get_settings.cache_clear()


def test_catalog_env_override_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LEGAL_AI_TIER_CATALOG_JSON", "{not valid json")
    get_settings.cache_clear()
    try:
        assert catalog.default_model("gratuit") == "ollama/gemma4:12b"
    finally:
        get_settings.cache_clear()


def test_shared_daily_budget_defaults_for_every_legacy_tier():
    for tier in catalog.TIER_ORDER:
        config = catalog.get_tier(tier)
        assert config["daily_token_budget"] == 5_000_000
        assert config["daily_request_budget"] == 30
        assert config["features"]["drafting"] is True
        assert config["features"]["priority"] is True


def test_global_budget_override_merges_for_every_legacy_tier():
    try:
        catalog.set_budget_overrides({"daily_token_budget": 2_000_000})
        for tier in catalog.TIER_ORDER:
            assert catalog.get_tier(tier)["daily_token_budget"] == 2_000_000
            assert catalog.get_tier(tier)["daily_request_budget"] == 30
        assert catalog.TIER_CATALOG["gratuit"]["daily_token_budget"] == 5_000_000
        assert catalog.effective_daily_budgets() == {
            "daily_token_budget": 2_000_000,
            "daily_request_budget": 30,
        }
        assert catalog.default_daily_budgets() == {
            "daily_token_budget": 5_000_000,
            "daily_request_budget": 30,
        }
    finally:
        catalog.set_budget_overrides({})
    assert catalog.get_tier("gratuit")["daily_token_budget"] == 5_000_000


def test_parse_budget_overrides_filters_garbage():
    assert catalog.parse_budget_overrides(None) == {}
    assert catalog.parse_budget_overrides("") == {}
    assert catalog.parse_budget_overrides("{not json") == {}
    assert catalog.parse_budget_overrides('["gratuit"]') == {}
    assert catalog.parse_budget_overrides('{"gratuit": {"daily_token_budget": 5}}') == {}
    parsed = catalog.parse_budget_overrides(
        json.dumps(
            {
                "daily_token_budget": 5,
                "daily_request_budget": -3,
                "rate_limit_per_minute": 1,
                "bad": True,
            }
        )
    )
    assert parsed == {"daily_token_budget": 5}


class _FakeSettingStore:
    """Minimal async stand-in for UserStore.get_setting."""

    def __init__(self, value: str | None = None, error: Exception | None = None):
        self.value = value
        self.error = error
        self.reads = 0

    async def get_setting(self, key: str):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.value


async def test_budget_overrides_refresh_after_ttl(monkeypatch):
    """A stale override cache is re-read from the DB; a fresh one is not."""
    store = _FakeSettingStore(json.dumps({"daily_token_budget": 7}))
    try:
        catalog.set_budget_overrides({})
        await catalog.refresh_budget_overrides(store)
        assert store.reads == 0
        assert catalog.get_tier("gratuit")["daily_token_budget"] == 5_000_000
        monkeypatch.setattr(catalog, "_budget_overrides_loaded_at", 0.0)
        await catalog.refresh_budget_overrides(store)
        assert store.reads == 1
        assert catalog.get_tier("cabinet")["daily_token_budget"] == 7
    finally:
        catalog.set_budget_overrides({})


async def test_budget_overrides_refresh_never_raises(monkeypatch):
    """A DB outage keeps the last known values and resets the TTL clock."""
    store = _FakeSettingStore(error=RuntimeError("db down"))
    catalog.set_budget_overrides({"daily_request_budget": 9})
    monkeypatch.setattr(catalog, "_budget_overrides_loaded_at", 0.0)
    try:
        await catalog.refresh_budget_overrides(store)  # must not raise
        assert catalog.get_tier("pro")["daily_request_budget"] == 9
        assert not catalog.budget_overrides_stale()
    finally:
        catalog.set_budget_overrides({})


def test_default_daily_budgets_allow_legacy_quota_override(monkeypatch):
    override = {
        "gratuit": {
            "providers": ["mock"],
            "models": [{"id": "mock/gratuit-model", "provider": "mock"}],
            "features": {},
            "daily_token_budget": 42,
            "daily_request_budget": 3,
        }
    }
    monkeypatch.setenv("LEGAL_AI_TIER_CATALOG_JSON", json.dumps(override))
    get_settings.cache_clear()
    try:
        assert catalog.default_daily_budgets() == {"daily_token_budget": 42, "daily_request_budget": 3}
        assert catalog.get_tier("pro")["daily_token_budget"] == 42
        assert catalog.get_tier("pro")["daily_request_budget"] == 3
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# LLMClient: OpenRouter + overrides
# ---------------------------------------------------------------------------


def test_llm_client_default_behavior_unchanged():
    settings = Settings(llm_provider="mock", llm_model="gpt-4o-mini")
    client = LLMClient(settings)
    assert client.provider == "mock"
    assert client.model == "mock/gpt-4o-mini"


def test_llm_client_strips_provider_namespace():
    settings = Settings(llm_provider="mock")
    assert LLMClient(settings, provider="ollama", model="ollama/gpt-oss:20b").model == "ollama/gpt-oss:20b"
    with pytest.raises(ValueError, match="unsupported chat provider"):
        LLMClient(settings, provider="openrouter", model="openrouter/openai/gpt-oss-20b:free")


# ---------------------------------------------------------------------------
# resolve_llm
# ---------------------------------------------------------------------------


def _user(tier: str, sub: str = "user-1") -> TokenPayload:
    return TokenPayload(sub=sub, role=Role.USER, exp=9_999_999_999, user_id=sub, tier=tier)


def test_resolve_llm_ignores_legacy_model_selection():
    settings = Settings(llm_provider="ollama")
    ctx = SimpleNamespace(settings=settings, llm=None)
    for requested in (
        "openrouter/openai/gpt-oss-20b:free",
        "tokenfree/gemini-2.5-flash",
        "openrouter/openai/gpt-99",
    ):
        client = resolve_llm(ctx, _user("pro"), requested)
        assert client.provider == "ollama"
        assert client.model == "ollama/gemma4:12b"
        assert client.api_key == ""


def test_resolve_llm_defaults_to_fixed_model():
    ctx = SimpleNamespace(settings=Settings(llm_provider="ollama"), llm=None)
    assert resolve_llm(ctx, _user("gratuit")).model == "ollama/gemma4:12b"
    assert resolve_llm(ctx, None).model == "ollama/gemma4:12b"
    assert resolve_llm(ctx, _user("pro")).model == "ollama/gemma4:12b"


def test_resolve_llm_mock_mode_keeps_ctx_llm_for_all_models():
    ctx = SimpleNamespace(settings=Settings(llm_provider="mock"), llm="MOCK-LLM")
    assert resolve_llm(ctx, _user("gratuit"), "openrouter/google/gemma-4-26b-a4b-it:free") == "MOCK-LLM"
    assert resolve_llm(ctx, _user("gratuit"), "tokenfree/gemini-2.5-flash") == "MOCK-LLM"


# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------


async def test_user_store_crud(tmp_path):
    from backend.users.service import UserStore

    store = UserStore(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/users.db", data_dir=tmp_path))
    record = await store.create_user("Test@Example.com", PASSWORD, "Test")
    assert record.email == "test@example.com"
    assert record.role == Role.USER
    assert record.tier == "gratuit"
    assert record.is_active is True
    assert record.workspace_id

    assert await store.authenticate("test@example.com", PASSWORD) is not None
    assert await store.authenticate("test@example.com", "wrong-password") is None
    assert await store.authenticate("nobody@example.com", PASSWORD) is None
    assert await store.set_active(record.id, False)
    assert await store.authenticate("test@example.com", PASSWORD) is None
    assert await store.set_active(record.id, True)
    assert await store.authenticate("test@example.com", PASSWORD) is not None

    with pytest.raises(UserAlreadyExistsError):
        await store.create_user("test@example.com", PASSWORD)

    await store.set_tier(record.id, "pro")
    loaded = await store.get_by_id(record.id)
    assert loaded is not None and loaded.tier == "pro"


# ---------------------------------------------------------------------------
# Auth API: register / token / me
# ---------------------------------------------------------------------------


def test_register_returns_token_and_me_profile(client):
    email, token = _register(client, name="Awa")
    response = client.get("/api/v1/auth/me", headers=_headers(token))
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == email
    assert data["name"] == "Awa"
    assert data["role"] == "user"
    assert data["tier"] == "gratuit"
    assert data["workspace_id"]


def test_register_duplicate_email_returns_400(client):
    email, _ = _register(client)
    response = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 400


def test_token_endpoint_authenticates_db_user_by_email(client):
    email, _ = _register(client)
    response = client.post("/api/v1/auth/token", json={"username": email, "password": PASSWORD})
    assert response.status_code == 200
    me = client.get("/api/v1/auth/me", headers=_headers(response.json()["access_token"]))
    assert me.json()["email"] == email


def test_dev_admin_token_still_works_with_cabinet_tier(client):
    response = client.post("/api/v1/auth/token", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    me = client.get("/api/v1/auth/me", headers=_headers(response.json()["access_token"]))
    assert me.json()["role"] == "admin"
    assert me.json()["tier"] == "cabinet"


def test_token_endpoint_rejects_bad_credentials(client):
    response = client.post("/api/v1/auth/token", json={"username": "admin", "password": "nope-nope"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Chat gating
# ---------------------------------------------------------------------------


def test_gratuit_user_allowed_premium_model_on_chat(client):
    """Dev mode: all tiers share the full catalog, so gratuit can pick any model."""
    _, token = _register(client)
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement ?", "model": "openrouter/google/gemma-4-26b-a4b-it:free"},
        headers=_headers(token),
    )
    assert response.status_code == 200


def test_chat_allows_every_model_for_gratuit_user(client):
    _, token = _register(client)
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement ?", "model": "tokenfree/gemini-2.5-flash"},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text


def test_billing_endpoints_are_removed(client):
    _, token = _register(client)
    headers = _headers(token)
    assert client.get("/api/v1/billing/config").status_code == 404
    assert client.get("/api/v1/billing/subscription", headers=headers).status_code == 404
    assert client.post("/api/v1/billing/checkout", json={"tier": "pro"}, headers=headers).status_code == 404


def test_chat_unknown_model_falls_back_to_default(client):
    """A stale model id stored on an old client must not brick the request."""
    _, token = _register(client)
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement ?", "model": "openrouter/openai/gpt-99"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["answer"]["answer"].strip()


def test_gratuit_default_chat_still_works(client):
    _, token = _register(client)
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement ?"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["answer"]["answer"]


def test_db_tier_change_unlocks_models_without_new_token(client):
    """Tier is re-read from the DB per request, not frozen in the JWT."""
    email, token = _register(client)
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    asyncio.run(client.app.state.ctx.user_store.set_tier(me["id"], "pro"))

    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement ?", "model": "openrouter/openai/gpt-oss-20b:free"},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    refreshed = client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert refreshed["tier"] == "pro"


# ---------------------------------------------------------------------------
# /models endpoint
# ---------------------------------------------------------------------------


def test_models_endpoint_anonymous_gets_fixed_read_only_catalog(client):
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["default_model"] == "ollama/gemma4:12b"
    assert data["selectable"] is False
    by_id = {m["id"]: m for m in data["models"]}
    assert set(by_id) == {"ollama/gemma4:12b", "ollama/nemotron-3-nano:4b"}
    for model in by_id.values():
        assert model["allowed"] is True
        assert model["tier_required"] == "gratuit"


def test_models_endpoint_fixed_with_legacy_token_tier(client):
    settings = get_settings()
    token = create_access_token("dev-pro", Role.USER, settings, tier="pro")
    response = client.get("/api/v1/models", headers=_headers(token))
    data = response.json()
    assert data["selectable"] is False
    by_id = {m["id"]: m for m in data["models"]}
    assert set(by_id) == {"ollama/gemma4:12b", "ollama/nemotron-3-nano:4b"}
    assert "openrouter/openai/gpt-oss-20b:free" not in by_id
    assert "tokenfree/gemini-2.5-flash" not in by_id


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def test_export_markdown_document(client):
    payload = {
        "query": "Quel est le préavis de licenciement ?",
        "session_id": "session-1",
        "answer": {
            "answer": "Voici la **réponse** détaillée.",
            "confidence": 0.9,
            "language": "fr",
            "citations": [
                {
                    "label": "Code du travail",
                    "document_name": "Code du travail du Burkina Faso",
                    "article": "123",
                    "url": "https://example.gov.bf/code-travail",
                    "verified": True,
                },
                {"label": "Source douteuse", "verified": False},
            ],
            "warnings": ["Confiance faible sur un point."],
        },
    }
    response = client.post("/api/v1/export/md", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "reponse-juridique" in response.headers["content-disposition"]

    body = response.text
    assert body.startswith("# Réponse juridique")
    assert "Voici la **réponse** détaillée." in body  # markdown preserved, not stripped
    assert "## Références" in body
    assert "art. 123" in body
    assert "https://example.gov.bf/code-travail" in body
    assert "Source douteuse" not in body  # unverified citations excluded
    assert "## Avertissements" in body
    assert "Confiance faible sur un point." in body
    assert "ne constitue pas un conseil juridique" in body
