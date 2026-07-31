"""Phase 6 tests: strict infra mode, readiness probes, per-tier rate limits.

Fully offline: "unreachable" deps point at closed localhost ports / missing
packages, which is exactly the degraded path being tested.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import contextmanager

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core import catalog  # noqa: E402
from backend.core.config import get_settings  # noqa: E402
from backend.core.models import Role  # noqa: E402
from backend.security.jwt import create_access_token  # noqa: E402

PASSWORD = "motdepasse1"

_BAD_INFRA_ENV = {
    "LEGAL_AI_MILVUS_ENABLED": "true",
    "LEGAL_AI_REDIS_ENABLED": "true",
    # Closed ports -> connection refused instantly (no slow timeouts), and
    # immune to any service the dev machine happens to run locally.
    "LEGAL_AI_REDIS_URL": "redis://127.0.0.1:5498/0",
    "LEGAL_AI_DATABASE_URL": "postgresql+psycopg://legal:legal@127.0.0.1:5499/legal_ai",
}


@contextmanager
def _make_client(tmp_path, monkeypatch, extra_env: dict | None = None):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/hardening.db")
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


def _register(client) -> str:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Awa"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Strict infra mode & readiness
# ---------------------------------------------------------------------------


def test_ready_strict_mode_returns_503_with_unreachable_deps(tmp_path, monkeypatch):
    env = {**_BAD_INFRA_ENV, "LEGAL_AI_STRICT_INFRA": "true"}
    with _make_client(tmp_path, monkeypatch, env) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["milvus"].startswith("degraded")
        assert data["checks"]["postgres"].startswith("degraded")
        assert data["checks"]["redis"].startswith("degraded")
        assert data["checks"]["database_probe"].startswith("degraded")


def test_ready_non_strict_same_outage_is_200_degraded(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, _BAD_INFRA_ENV) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["milvus"].startswith("degraded")
        assert data["checks"]["postgres"].startswith("degraded")


def test_ready_development_defaults_are_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["cache_probe"] == "ok"
    assert data["checks"]["vector_store_probe"] == "ok"
    assert data["checks"]["database_probe"] == "ok"
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# Per-tier rate limits
# ---------------------------------------------------------------------------


def _tiny_limit_catalog() -> str:
    override = json.loads(json.dumps(catalog.TIER_CATALOG))  # deep copy
    override["gratuit"]["rate_limit_per_minute"] = 2
    override["pro"]["rate_limit_per_minute"] = 4
    override["cabinet"]["rate_limit_per_minute"] = 1000
    return json.dumps(override)


_LIMIT_ENV = {
    "LEGAL_AI_RATE_LIMIT_PER_MINUTE": "3",  # anonymous/IP bucket
    "LEGAL_AI_TIER_CATALOG_JSON": _tiny_limit_catalog(),
}


def test_per_tier_rate_limits(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, _LIMIT_ENV) as client:
        gratuit = _register(client)  # counts once toward the anonymous IP bucket

        # gratuit: limit 2 -> the third call is rejected.
        assert client.get("/api/v1/models", headers=_headers(gratuit)).status_code == 200
        assert client.get("/api/v1/models", headers=_headers(gratuit)).status_code == 200
        response = client.get("/api/v1/models", headers=_headers(gratuit))
        assert response.status_code == 429
        assert "Retry-After" in response.headers

        # dev admin (cabinet, limit 1000): unaffected by the gratuit bucket.
        admin = client.post(
            "/api/v1/auth/token", json={"username": "admin", "password": "admin123"}
        ).json()["access_token"]
        for _ in range(4):
            assert client.get("/api/v1/models", headers=_headers(admin)).status_code == 200

        # anonymous IP bucket (limit 3): register + token exchange used 2,
        # so one more anonymous call passes, the next is rejected.
        assert client.get("/api/v1/models").status_code == 200
        assert client.get("/api/v1/models").status_code == 429

        # Probes stay exempt even with every bucket exhausted.
        for path in ("/health", "/ready", "/metrics"):
            assert client.get(path).status_code == 200


def test_jwt_tier_claim_drives_limit_without_db(tmp_path, monkeypatch):
    """A crafted token's tier claim sets the limit — no user in the DB."""
    with _make_client(tmp_path, monkeypatch, _LIMIT_ENV) as client:
        settings = get_settings()
        cabinet = create_access_token("crafted-cabinet", Role.USER, settings, tier="cabinet")
        gratuit = create_access_token("crafted-gratuit", Role.USER, settings, tier="gratuit")

        for _ in range(5):
            assert client.get("/api/v1/models", headers=_headers(cabinet)).status_code == 200

        assert client.get("/api/v1/models", headers=_headers(gratuit)).status_code == 200
        assert client.get("/api/v1/models", headers=_headers(gratuit)).status_code == 200
        assert client.get("/api/v1/models", headers=_headers(gratuit)).status_code == 429


def test_invalid_token_falls_back_to_anonymous_bucket(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, _LIMIT_ENV) as client:
        bad = _headers("not-a-real-token")
        for expected in (200, 200, 200, 429):  # anonymous limit is 3
            assert client.get("/api/v1/models", headers=bad).status_code == expected


# ---------------------------------------------------------------------------
# Redis-shared rate limiting (multi-worker path)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async INCR/EXPIRE double (no fakeredis dependency)."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expirations[key] = ttl
        return True


def test_shared_redis_rate_limit_counter():
    """The Redis path shares one fixed-window counter across workers."""
    from backend.api.middleware import _allow_shared
    from backend.core.cache import RedisCache

    cache = RedisCache.__new__(RedisCache)  # no connection: inject the double
    cache._redis = _FakeRedis()

    allowed, _ = asyncio.run(_allow_shared(cache, "user:x", 2))
    assert allowed is True
    allowed, _ = asyncio.run(_allow_shared(cache, "user:x", 2))
    assert allowed is True
    allowed, retry_after = asyncio.run(_allow_shared(cache, "user:x", 2))
    assert allowed is False
    assert retry_after >= 1
    # TTL set on first increment so buckets expire.
    assert any(key.startswith("ratelimit:user:x:") for key in cache._redis.expirations)


def test_shared_redis_path_fails_open_on_error():
    from backend.api.middleware import _allow_shared
    from backend.core.cache import RedisCache

    class _BrokenRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")

    cache = RedisCache.__new__(RedisCache)
    cache._redis = _BrokenRedis()
    allowed, _ = asyncio.run(_allow_shared(cache, "user:x", 1))
    assert allowed is True  # documented fail-open
