"""Mobile session binding, account lifecycle and password reset tests.

Covers the mobile-readiness changes (see docs/mobile-app.md):
- ``X-Device-Id`` header -> IP-independent fingerprint + per-device-class
  session scope (web and mobile no longer kick each other out);
- ``POST /auth/logout`` and ``DELETE /auth/me``;
- token-based password reset (mailer monkeypatched — fully offline).
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.core.config import get_settings  # noqa: E402
from backend.security.sessions import device_fingerprint  # noqa: E402

PASSWORD = "motdepasse1"


@contextmanager
def _make_client(tmp_path, monkeypatch, extra_env: dict | None = None):
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/mobile.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_RATE_LIMIT_PER_MINUTE", "1000000")
    monkeypatch.setenv("LEGAL_AI_RATE_LIMIT_PER_SECOND", "1000000")
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


@pytest.fixture
def sso_client(tmp_path, monkeypatch):
    """Client with single-session-per-user enforcement enabled."""
    with _make_client(tmp_path, monkeypatch, {"LEGAL_AI_SINGLE_SESSION_PER_USER": "true"}) as c:
        yield c


def _register(client, headers: dict | None = None) -> tuple[str, str]:
    """Register a fresh account; returns (email, token)."""
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Awa"},
        headers=headers or {},
    )
    assert response.status_code == 201, response.text
    return email, response.json()["access_token"]


def _login(client, email: str, password: str = PASSWORD, headers: dict | None = None):
    return client.post(
        "/api/v1/auth/token",
        json={"username": email, "password": password},
        headers=headers or {},
    )


def _auth(token: str, device_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if device_id:
        headers["X-Device-Id"] = device_id
    return headers


# ---------------------------------------------------------------------------
# A1 — mobile session binding
# ---------------------------------------------------------------------------


def test_device_fingerprint_ignores_ip_when_device_id_present():
    """CGNAT / Wi-Fi<->cellular handover must not change the fingerprint."""
    headers = {"x-device-id": "device-123", "user-agent": "Yawoto/1.0"}
    req_a = SimpleNamespace(headers=headers, client=SimpleNamespace(host="10.0.0.1"))
    req_b = SimpleNamespace(headers=headers, client=SimpleNamespace(host="172.16.9.9"))
    assert device_fingerprint(req_a) == device_fingerprint(req_b)


def test_device_fingerprint_web_includes_ip():
    headers = {"user-agent": "Mozilla/5.0", "accept-language": "fr"}
    req_a = SimpleNamespace(headers=headers, client=SimpleNamespace(host="10.0.0.1"))
    req_b = SimpleNamespace(headers=headers, client=SimpleNamespace(host="172.16.9.9"))
    assert device_fingerprint(req_a) != device_fingerprint(req_b)


def test_web_and_mobile_sessions_coexist(sso_client):
    """A mobile login must not invalidate the web session (and vice versa)."""
    email, web_token = _register(sso_client)

    mobile_login = _login(sso_client, email, headers={"X-Device-Id": "phone-A"})
    assert mobile_login.status_code == 200, mobile_login.text
    mobile_token = mobile_login.json()["access_token"]

    assert sso_client.get("/api/v1/auth/me", headers=_auth(web_token)).status_code == 200
    assert (
        sso_client.get("/api/v1/auth/me", headers=_auth(mobile_token, "phone-A")).status_code == 200
    )


def test_second_mobile_device_kicks_first_only(sso_client):
    email, web_token = _register(sso_client)

    token_a = _login(sso_client, email, headers={"X-Device-Id": "phone-A"}).json()["access_token"]
    token_b = _login(sso_client, email, headers={"X-Device-Id": "phone-B"}).json()["access_token"]

    assert sso_client.get("/api/v1/auth/me", headers=_auth(token_a, "phone-A")).status_code == 401
    assert sso_client.get("/api/v1/auth/me", headers=_auth(token_b, "phone-B")).status_code == 200
    # The web session survives the mobile churn.
    assert sso_client.get("/api/v1/auth/me", headers=_auth(web_token)).status_code == 200


def test_mobile_refresh_keeps_session(sso_client):
    email, _ = _register(sso_client)
    token = _login(sso_client, email, headers={"X-Device-Id": "phone-A"}).json()["access_token"]

    renewed = sso_client.post("/api/v1/auth/refresh", headers=_auth(token, "phone-A"))
    assert renewed.status_code == 200, renewed.text
    new_token = renewed.json()["access_token"]
    assert sso_client.get("/api/v1/auth/me", headers=_auth(new_token, "phone-A")).status_code == 200


# ---------------------------------------------------------------------------
# A2 — logout & account deletion
# ---------------------------------------------------------------------------


def test_logout_revokes_current_scope_only(sso_client):
    email, web_token = _register(sso_client)
    mobile_token = _login(sso_client, email, headers={"X-Device-Id": "phone-A"}).json()[
        "access_token"
    ]

    assert (
        sso_client.post("/api/v1/auth/logout", headers=_auth(mobile_token, "phone-A")).status_code
        == 204
    )
    # Mobile token is dead; the web session was logged out on neither side.
    assert (
        sso_client.get("/api/v1/auth/me", headers=_auth(mobile_token, "phone-A")).status_code == 401
    )
    assert sso_client.get("/api/v1/auth/me", headers=_auth(web_token)).status_code == 200


def test_delete_account_flow(sso_client):
    email, token = _register(sso_client)

    assert sso_client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 204
    # Old credentials no longer authenticate, and the revoked token 401s.
    assert _login(sso_client, email).status_code == 401
    assert sso_client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401
    # The identifier is free again (row actually gone).
    re_register = sso_client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": "Awa"}
    )
    assert re_register.status_code == 201, re_register.text


def test_delete_account_rejects_dev_store_user(client):
    """The dev bootstrap admin is not a DB account -> cannot self-delete."""
    login = _login(client, "admin", password="admin123")
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    assert client.delete("/api/v1/auth/me", headers=_auth(token)).status_code == 400


def test_delete_account_protects_last_admin(client):
    _, admin_token = _register(client)
    admin_id = client.get("/api/v1/auth/me", headers=_auth(admin_token)).json()["id"]

    ctx = client.app.state.ctx
    assert client.portal is not None
    assert client.portal.call(ctx.user_store.set_role, admin_id, "admin") is True

    # Sole DB admin: refused. (The dev-store bootstrap admin is not in the DB.)
    assert client.delete("/api/v1/auth/me", headers=_auth(admin_token)).status_code == 403

    # With a second admin, deletion succeeds.
    _, other_token = _register(client)
    other_id = client.get("/api/v1/auth/me", headers=_auth(other_token)).json()["id"]
    assert client.portal.call(ctx.user_store.set_role, other_id, "admin") is True
    assert client.delete("/api/v1/auth/me", headers=_auth(admin_token)).status_code == 204


# ---------------------------------------------------------------------------
# A3 — password reset
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_emails(monkeypatch):
    """Intercept outbound mail: returns a list of (to, subject, body)."""
    sent: list[tuple[str, str, str]] = []

    async def _fake_send(_settings, to, subject, body):
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr("backend.api.routers.auth.send_email", _fake_send)
    return sent


def _token_from_body(body: str) -> str:
    match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert match, f"no reset token in email body: {body!r}"
    return match.group(1)


def test_password_reset_full_flow(client, captured_emails):
    email, _ = _register(client)

    response = client.post("/api/v1/auth/password-reset/request", json={"identifier": email})
    assert response.status_code == 202, response.text
    assert len(captured_emails) == 1
    to, _, body = captured_emails[0]
    assert to == email
    token = _token_from_body(body)

    confirm = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "nouveaumdp2"},
    )
    assert confirm.status_code == 200, confirm.text

    assert _login(client, email).status_code == 401  # old password dead
    assert _login(client, email, password="nouveaumdp2").status_code == 200


def test_password_reset_request_is_enumeration_safe(client, captured_emails):
    response = client.post(
        "/api/v1/auth/password-reset/request", json={"identifier": "personne@example.com"}
    )
    assert response.status_code == 202
    assert captured_emails == []


def test_password_reset_confirm_rejects_bad_token(client):
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-token-xx", "new_password": "nouveaumdp2"},
    )
    assert response.status_code == 400


def test_password_reset_revokes_sessions(sso_client, captured_emails):
    email, token = _register(sso_client)
    client = sso_client
    client.post("/api/v1/auth/password-reset/request", json={"identifier": email})
    reset_token = _token_from_body(captured_emails[0][2])
    assert (
        client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": reset_token, "new_password": "nouveaumdp2"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401
