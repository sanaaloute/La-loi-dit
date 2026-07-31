"""Phase 7 tests: Paddle billing (checkout, webhook HMAC, tier lifecycle).

Fully offline: billing-disabled paths use the default config; enabled paths
set Paddle env vars and mock the httpx client — no network ever happens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core.config import get_settings  # noqa: E402

PASSWORD = "motdepasse1"
WEBHOOK_SECRET = "whsec_test_secret"

_BILLING_ENV = {
    "LEGAL_AI_PADDLE_ENABLED": "true",
    "LEGAL_AI_PADDLE_ENV": "sandbox",
    "LEGAL_AI_PADDLE_API_KEY": "pdl_test_api_key",
    "LEGAL_AI_PADDLE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "LEGAL_AI_PADDLE_PRICE_PRO": "pri_pro_123",
    "LEGAL_AI_PADDLE_PRICE_CABINET": "pri_cabinet_456",
}


@contextmanager
def _make_client(tmp_path, monkeypatch, extra_env: dict | None = None):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/billing.db")
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
    """Billing DISABLED (default) app."""
    with _make_client(tmp_path, monkeypatch) as test_client:
        yield test_client


@pytest.fixture
def billing_client(tmp_path, monkeypatch):
    """Billing enabled (sandbox config) app."""
    with _make_client(tmp_path, monkeypatch, _BILLING_ENV) as test_client:
        yield test_client


def _register(client) -> tuple[str, str]:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Awa"},
    )
    assert response.status_code == 201, response.text
    return email, response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sign(body: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts},h1={digest}"


def _webhook(client, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/v1/billing/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Paddle-Signature": signature if signature is not None else _sign(body),
        },
    )


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------


def test_config_disabled_by_default(client):
    data = client.get("/api/v1/billing/config").json()
    assert data == {"enabled": False, "provider": None}


def test_config_enabled(billing_client):
    data = billing_client.get("/api/v1/billing/config").json()
    assert data == {"enabled": True, "provider": "paddle"}


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def test_checkout_503_when_disabled(client):
    _, token = _register(client)
    response = client.post("/api/v1/billing/checkout", json={"tier": "pro"}, headers=_headers(token))
    assert response.status_code == 503


def test_checkout_rejects_gratuit_and_anonymous(billing_client):
    _, token = _register(billing_client)
    response = billing_client.post(
        "/api/v1/billing/checkout", json={"tier": "gratuit"}, headers=_headers(token)
    )
    assert response.status_code == 400
    assert billing_client.post("/api/v1/billing/checkout", json={"tier": "pro"}).status_code == 401


def test_checkout_returns_url_and_sends_correct_payload(billing_client, monkeypatch):
    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"id": "txn_1", "checkout": {"url": "https://checkout.paddle.test/txn_1"}}}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured.update({"url": url, "json": json, "headers": headers})
            return _FakeResponse()

    monkeypatch.setattr("backend.billing.paddle.httpx.AsyncClient", _FakeAsyncClient)

    email, token = _register(billing_client)
    response = billing_client.post(
        "/api/v1/billing/checkout", json={"tier": "pro"}, headers=_headers(token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["checkout_url"] == "https://checkout.paddle.test/txn_1"

    assert captured["url"] == "https://sandbox-api.paddle.com/transactions"
    assert captured["headers"]["Authorization"] == "Bearer pdl_test_api_key"
    payload = captured["json"]
    assert payload["items"] == [{"price_id": "pri_pro_123", "quantity": 1}]
    assert payload["customer"]["email"] == email
    me = billing_client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert payload["custom_data"] == {"user_id": me["id"]}


# ---------------------------------------------------------------------------
# Webhook: signature verification
# ---------------------------------------------------------------------------


def test_webhook_503_when_disabled(client):
    response = _webhook(client, {"event_type": "transaction.completed", "data": {}})
    assert response.status_code == 503


def test_webhook_rejects_tampered_body(billing_client):
    body = json.dumps({"event_type": "transaction.completed", "data": {}}).encode()
    signature = _sign(body)
    tampered = body.replace(b"completed", b"canceledX")
    response = billing_client.post(
        "/api/v1/billing/webhook",
        content=tampered,
        headers={"Content-Type": "application/json", "Paddle-Signature": signature},
    )
    assert response.status_code == 401


def test_webhook_rejects_stale_timestamp(billing_client):
    body = json.dumps({"event_type": "transaction.completed", "data": {}}).encode()
    stale = _sign(body, ts=int(time.time()) - 600)
    response = billing_client.post(
        "/api/v1/billing/webhook",
        content=body,
        headers={"Content-Type": "application/json", "Paddle-Signature": stale},
    )
    assert response.status_code == 401


def test_webhook_unknown_event_type_ignored(billing_client):
    response = _webhook(billing_client, {"event_type": "adjustment.created", "data": {}})
    assert response.status_code == 200
    assert response.json()["received"] is True


# ---------------------------------------------------------------------------
# Webhook: subscription lifecycle
# ---------------------------------------------------------------------------


def _completed_payload(user_id: str) -> dict:
    return {
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_100",
            "customer_id": "ctm_100",
            "subscription_id": "sub_100",
            "custom_data": {"user_id": user_id},
            "items": [{"price_id": "pri_pro_123", "quantity": 1}],
        },
    }


def test_transaction_completed_upgrades_tier(billing_client):
    _, token = _register(billing_client)
    me = billing_client.get("/api/v1/auth/me", headers=_headers(token)).json()

    response = _webhook(billing_client, _completed_payload(me["id"]))
    assert response.status_code == 200
    assert response.json() == {"received": True, "applied": True}

    me = billing_client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert me["tier"] == "pro"

    subscription = billing_client.get("/api/v1/billing/subscription", headers=_headers(token)).json()
    assert subscription["tier"] == "pro"
    assert subscription["status"] == "active"
    assert subscription["cancel_at_period_end"] is False

    # Paddle ids stored, enabling customer-id resolution for later events.
    import asyncio

    record = asyncio.run(billing_client.app.state.ctx.user_store.get_by_id(me["id"]))
    assert record.paddle_customer_id == "ctm_100"
    assert record.paddle_subscription_id == "sub_100"

    # Idempotent replay: same event again changes nothing.
    response = _webhook(billing_client, _completed_payload(me["id"]))
    assert response.status_code == 200
    me = billing_client.get("/api/v1/auth/me", headers=_headers(token)).json()
    assert me["tier"] == "pro"


def test_subscription_canceled_past_period_end_downgrades(billing_client):
    _, token = _register(billing_client)
    me = billing_client.get("/api/v1/auth/me", headers=_headers(token)).json()
    _webhook(billing_client, _completed_payload(me["id"]))

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    payload = {
        "event_type": "subscription.canceled",
        "data": {
            "id": "sub_100",
            "customer_id": "ctm_100",
            "custom_data": {"user_id": me["id"]},
            "current_billing_period": {"ends_at": past},
            "items": [{"price_id": "pri_pro_123"}],
        },
    }
    response = _webhook(billing_client, payload)
    assert response.status_code == 200

    subscription = billing_client.get("/api/v1/billing/subscription", headers=_headers(token)).json()
    assert subscription["tier"] == "gratuit"
    assert subscription["status"] == "canceled"


def test_subscription_canceled_future_period_end_keeps_tier(billing_client):
    _, token = _register(billing_client)
    me = billing_client.get("/api/v1/auth/me", headers=_headers(token)).json()
    _webhook(billing_client, _completed_payload(me["id"]))

    future = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    payload = {
        "event_type": "subscription.canceled",
        "data": {
            "id": "sub_100",
            "customer_id": "ctm_100",
            "custom_data": {"user_id": me["id"]},
            "current_billing_period": {"ends_at": future},
            "items": [{"price_id": "pri_pro_123"}],
        },
    }
    assert _webhook(billing_client, payload).status_code == 200

    subscription = billing_client.get("/api/v1/billing/subscription", headers=_headers(token)).json()
    assert subscription["tier"] == "pro"  # paid until period end
    assert subscription["status"] == "canceled"
    assert subscription["current_period_end"] == future
