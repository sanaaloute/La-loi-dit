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
from backend.core.exceptions import AuthorizationError, UserAlreadyExistsError  # noqa: E402
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


def test_catalog_tiers_restricted_in_dev_mode():
    """Gratuit only sees OpenRouter + Ollama; paid tiers see the full catalog."""
    gratuit = {m.id for m in catalog.allowed_models("gratuit")}
    pro = {m.id for m in catalog.allowed_models("pro")}
    cabinet = {m.id for m in catalog.allowed_models("cabinet")}
    assert gratuit < pro  # strict subset
    assert pro == cabinet
    assert all(m.provider in ("ollama", "openrouter") for m in catalog.allowed_models("gratuit"))
    assert any(m.provider == "tokenfree" for m in catalog.allowed_models("pro"))


def test_is_model_allowed_per_tier():
    assert catalog.is_model_allowed("gratuit", "ollama/gpt-oss:20b")
    assert catalog.is_model_allowed("gratuit", "openrouter/openai/gpt-oss-20b:free")
    assert catalog.is_model_allowed("pro", "openrouter/openai/gpt-oss-20b:free")
    assert catalog.is_model_allowed("pro", "openrouter/google/gemma-4-26b-a4b-it:free")
    assert catalog.is_model_allowed("cabinet", "openrouter/google/gemma-4-26b-a4b-it:free")
    assert not catalog.is_model_allowed("cabinet", "openrouter/openai/gpt-99")


def test_default_model_and_unknown_tier():
    # Default = the first Ollama Cloud catalog entry, for every tier (users
    # can pick another model from the UI; cheap routing handles trivial
    # queries). Gratuit catalog is OpenRouter + Ollama only.
    assert catalog.default_model("gratuit") == "ollama/gpt-oss:20b"
    assert catalog.default_model("pro") == "ollama/gpt-oss:20b"
    assert catalog.get_tier("inconnu") == catalog.get_tier("gratuit")


def test_all_models_with_access_annotations():
    annotated = {m["id"]: m for m in catalog.all_models_with_access("pro")}
    assert annotated["ollama/gpt-oss:20b"]["allowed"] is True
    assert annotated["ollama/gpt-oss:20b"]["tier_required"] == "gratuit"
    assert annotated["openrouter/openai/gpt-oss-20b:free"]["allowed"] is True
    assert annotated["openrouter/openai/gpt-oss-20b:free"]["tier_required"] == "gratuit"
    assert annotated["openrouter/google/gemma-4-26b-a4b-it:free"]["allowed"] is True
    assert annotated["openrouter/google/gemma-4-26b-a4b-it:free"]["tier_required"] == "gratuit"
    # TokenFree models are above gratuit.
    assert annotated["tokenfree/gemini-2.5-flash"]["allowed"] is True
    assert annotated["tokenfree/gemini-2.5-flash"]["tier_required"] == "pro"


def test_catalog_env_override(monkeypatch):
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
        assert catalog.default_model("gratuit") == "mock/gratuit-model"
        assert catalog.is_model_allowed("gratuit", "mock/gratuit-model")
        assert not catalog.is_model_allowed("gratuit", "ollama/gpt-oss:20b")
    finally:
        get_settings.cache_clear()


def test_catalog_env_override_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LEGAL_AI_TIER_CATALOG_JSON", "{not valid json")
    get_settings.cache_clear()
    try:
        assert catalog.default_model("gratuit") == "ollama/gpt-oss:20b"
    finally:
        get_settings.cache_clear()


def test_default_daily_budgets_per_tier():
    """The built-in catalog carries the real per-tier production quotas."""
    assert catalog.get_tier("gratuit")["daily_token_budget"] == 1_000_000
    assert catalog.get_tier("pro")["daily_token_budget"] == 10_000_000
    assert catalog.get_tier("cabinet")["daily_token_budget"] == 100_000_000
    assert catalog.get_tier("gratuit")["daily_request_budget"] == 50
    assert catalog.get_tier("pro")["daily_request_budget"] == 500
    assert catalog.get_tier("cabinet")["daily_request_budget"] == 10_000


def test_budget_overrides_merge_into_get_tier():
    """Admin overrides win over the defaults without mutating the catalog."""
    try:
        catalog.set_budget_overrides({"gratuit": {"daily_token_budget": 2_000_000}})
        assert catalog.get_tier("gratuit")["daily_token_budget"] == 2_000_000
        # Untouched fields and tiers keep their catalog values.
        assert catalog.get_tier("gratuit")["daily_request_budget"] == 50
        assert catalog.get_tier("pro")["daily_token_budget"] == 10_000_000
        assert catalog.TIER_CATALOG["gratuit"]["daily_token_budget"] == 1_000_000
        assert catalog.effective_tier_budgets()["gratuit"]["daily_token_budget"] == 2_000_000
        assert catalog.default_tier_budgets()["gratuit"]["daily_token_budget"] == 1_000_000
    finally:
        catalog.set_budget_overrides({})
    assert catalog.get_tier("gratuit")["daily_token_budget"] == 1_000_000


def test_parse_budget_overrides_filters_garbage():
    assert catalog.parse_budget_overrides(None) == {}
    assert catalog.parse_budget_overrides("") == {}
    assert catalog.parse_budget_overrides("{not json") == {}
    assert catalog.parse_budget_overrides('["gratuit"]') == {}
    parsed = catalog.parse_budget_overrides(
        json.dumps(
            {
                "gratuit": {"daily_token_budget": 5, "rate_limit_per_minute": 1, "bad": True},
                "gold": {"daily_token_budget": 5},
                "pro": {"daily_token_budget": -3},
            }
        )
    )
    # Unknown tiers/fields and non-positive values are dropped.
    assert parsed == {"gratuit": {"daily_token_budget": 5}}


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
    store = _FakeSettingStore(json.dumps({"gratuit": {"daily_token_budget": 7}}))
    try:
        catalog.set_budget_overrides({})
        # Fresh cache: no DB read at all.
        await catalog.refresh_budget_overrides(store)
        assert store.reads == 0
        assert catalog.get_tier("gratuit")["daily_token_budget"] == 1_000_000
        # Stale cache (TTL expired): one indexed read, overrides applied —
        # this is how a PATCH served by another uvicorn worker propagates.
        monkeypatch.setattr(catalog, "_budget_overrides_loaded_at", 0.0)
        await catalog.refresh_budget_overrides(store)
        assert store.reads == 1
        assert catalog.get_tier("gratuit")["daily_token_budget"] == 7
    finally:
        catalog.set_budget_overrides({})


async def test_budget_overrides_refresh_never_raises(monkeypatch):
    """A DB outage keeps the last known values and resets the TTL clock."""
    store = _FakeSettingStore(error=RuntimeError("db down"))
    catalog.set_budget_overrides({"pro": {"daily_request_budget": 9}})
    monkeypatch.setattr(catalog, "_budget_overrides_loaded_at", 0.0)
    try:
        await catalog.refresh_budget_overrides(store)  # must not raise
        assert catalog.get_tier("pro")["daily_request_budget"] == 9
        # The TTL clock was bumped too: no per-request retry storm.
        assert not catalog.budget_overrides_stale()
    finally:
        catalog.set_budget_overrides({})


def test_default_tier_budgets_reflect_env_catalog(monkeypatch):
    """The admin UI "Par défaut" hints follow LEGAL_AI_TIER_CATALOG_JSON."""
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
        defaults = catalog.default_tier_budgets()
        assert defaults["gratuit"] == {"daily_token_budget": 42, "daily_request_budget": 3}
        # Tiers missing from the override fall back to its default tier,
        # exactly like get_tier does.
        assert defaults["pro"] == {"daily_token_budget": 42, "daily_request_budget": 3}
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
    assert (
        LLMClient(settings, provider="openrouter", model="openrouter/openai/gpt-oss-20b:free").model
        == "openrouter/openai/gpt-oss-20b:free"
    )


async def test_openrouter_completion_kwargs(monkeypatch):
    import backend.core.llm as llm_module

    settings = Settings(llm_provider="mock", llm_api_base="", llm_api_key="")
    client = LLMClient(
        settings,
        provider="openrouter",
        model="openrouter/openai/gpt-oss-20b:free",
        api_key="or-key",
    )
    captured: dict = {}

    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)
    assert await client.complete("system", "user") == "ok"
    assert captured["model"] == "openrouter/openai/gpt-oss-20b:free"
    assert captured["api_base"] == "https://openrouter.ai/api/v1"
    assert captured["api_key"] == "or-key"
    assert captured["extra_headers"]["HTTP-Referer"] == settings.app_name
    assert captured["extra_headers"]["X-Title"] == settings.app_name


async def test_openrouter_headers_are_ascii_safe(monkeypatch):
    """A non-ASCII app name (e.g. with an em-dash) must not break the call."""
    import backend.core.llm as llm_module

    settings = Settings(llm_provider="mock", app_name="Yawoto — Assistant Juridique")
    client = LLMClient(
        settings,
        provider="openrouter",
        model="openrouter/openai/gpt-oss-20b:free",
        api_key="or-key",
    )
    captured: dict = {}

    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)
    assert await client.complete("system", "user") == "ok"
    title = captured["extra_headers"]["X-Title"]
    title.encode("ascii")  # raises if any non-ASCII char survived
    assert "Yawoto" in title


async def test_tokenfree_completion_kwargs(monkeypatch):
    import backend.core.llm as llm_module

    settings = Settings(llm_provider="mock", llm_api_base="", llm_api_key="")
    client = LLMClient(
        settings,
        provider="tokenfree",
        model="tokenfree/gemini-2.5-flash",
        api_key="tf-key",
    )
    captured: dict = {}

    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)
    assert await client.complete("system", "user") == "ok"
    # OpenAI-compatible: "openai/" LiteLLM prefix + default TokenFree base URL.
    assert captured["model"] == "openai/gemini-2.5-flash"
    assert captured["api_base"] == "https://www.tokenfree.com/v1"
    assert captured["api_key"] == "tf-key"


def test_resolve_llm_tokenfree_uses_tokenfree_key():
    settings = Settings(llm_provider="openai", llm_api_key="sk-main", tokenfree_api_key="tf-test")
    ctx = SimpleNamespace(settings=settings, llm=None)
    # TokenFree models are not available on the gratuit tier.
    with pytest.raises(AuthorizationError, match="requires a higher subscription tier"):
        resolve_llm(ctx, _user("gratuit"), "tokenfree/gemini-2.5-flash")
    client = resolve_llm(ctx, _user("pro"), "tokenfree/gemini-2.5-flash")
    assert client.provider == "tokenfree"
    assert client.model == "openai/gemini-2.5-flash"
    assert client.api_key == "tf-test"
    assert client.api_base == "https://www.tokenfree.com/v1"


# ---------------------------------------------------------------------------
# resolve_llm
# ---------------------------------------------------------------------------


def _user(tier: str, sub: str = "user-1") -> TokenPayload:
    return TokenPayload(sub=sub, role=Role.USER, exp=9_999_999_999, user_id=sub, tier=tier)


def test_resolve_llm_allows_tier_model():
    settings = Settings(llm_provider="openai", llm_api_key="sk-test", openrouter_api_key="or-test")
    ctx = SimpleNamespace(settings=settings, llm=None)
    client = resolve_llm(ctx, _user("pro"), "openrouter/openai/gpt-oss-20b:free")
    assert client.provider == "openrouter"
    assert client.model == "openrouter/openai/gpt-oss-20b:free"
    assert client.api_key == "or-test"


def test_resolve_llm_denies_unknown_model():
    ctx = SimpleNamespace(settings=Settings(llm_provider="openai"), llm=None)
    with pytest.raises(AuthorizationError, match="requires a higher subscription tier"):
        resolve_llm(ctx, _user("gratuit"), "openrouter/openai/gpt-99")


def test_resolve_llm_defaults_to_tier_model():
    ctx = SimpleNamespace(settings=Settings(llm_provider="openai"), llm=None)
    # No query -> tier default (the Ollama Cloud catalog entry, every tier);
    # cheap routing needs a query.
    assert resolve_llm(ctx, _user("gratuit")).model == "ollama/gpt-oss:20b"
    assert resolve_llm(ctx, None).model == "ollama/gpt-oss:20b"  # anonymous
    assert resolve_llm(ctx, _user("pro")).model == "ollama/gpt-oss:20b"


def test_resolve_llm_mock_mode_keeps_ctx_llm_but_gates():
    ctx = SimpleNamespace(settings=Settings(llm_provider="mock"), llm="MOCK-LLM")
    assert resolve_llm(ctx, _user("gratuit"), "openrouter/google/gemma-4-26b-a4b-it:free") == "MOCK-LLM"
    with pytest.raises(AuthorizationError):
        resolve_llm(ctx, _user("gratuit"), "openrouter/openai/gpt-99")


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
    assert record.workspace_id

    assert await store.authenticate("test@example.com", PASSWORD) is not None
    assert await store.authenticate("test@example.com", "wrong-password") is None
    assert await store.authenticate("nobody@example.com", PASSWORD) is None

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


def test_chat_denies_unknown_model(client):
    _, token = _register(client)
    response = client.post(
        "/api/v1/chat",
        json={"query": "Quel est le préavis de licenciement ?", "model": "openrouter/openai/gpt-99"},
        headers=_headers(token),
    )
    assert response.status_code == 403
    assert "requires a higher subscription tier" in response.json()["detail"]


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


def test_models_endpoint_anonymous_sees_gratuit(client):
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["default_model"] == "ollama/gpt-oss:20b"
    by_id = {m["id"]: m for m in data["models"]}
    assert by_id["ollama/gpt-oss:20b"]["allowed"] is True
    assert by_id["openrouter/openai/gpt-oss-20b:free"]["allowed"] is True
    assert by_id["openrouter/google/gemma-4-26b-a4b-it:free"]["allowed"] is True
    assert by_id["openrouter/google/gemma-4-26b-a4b-it:free"]["tier_required"] == "gratuit"
    assert by_id["tokenfree/gemini-2.5-flash"]["allowed"] is False
    assert by_id["tokenfree/gemini-2.5-flash"]["tier_required"] == "pro"


def test_models_endpoint_respects_token_tier(client):
    settings = get_settings()
    token = create_access_token("dev-pro", Role.USER, settings, tier="pro")
    response = client.get("/api/v1/models", headers=_headers(token))
    by_id = {m["id"]: m for m in response.json()["models"]}
    assert by_id["openrouter/openai/gpt-oss-20b:free"]["allowed"] is True
    assert by_id["openrouter/google/gemma-4-26b-a4b-it:free"]["allowed"] is True


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
