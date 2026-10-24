import os

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core.config import get_settings


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """A TestClient with a fresh in-memory app and one dev user."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_REDIS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_MILVUS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    monkeypatch.setenv("LEGAL_AI_DEV_USERS", "admin:test:admin")
    monkeypatch.setenv("LEGAL_AI_JWT_REFRESH_EXPIRE_DAYS", "7")
    monkeypatch.setenv(
        "LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/legal_ai.db"
    )
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        yield client


def _login(client: TestClient, username: str = "admin", password: str = "test") -> dict:
    res = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    return data


def test_login_issues_access_and_refresh_tokens(auth_client):
    data = _login(auth_client)
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 60 * 60


def test_refresh_with_refresh_token_returns_new_access_token(auth_client):
    tokens = _login(auth_client)
    res = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["access_token"] != tokens["access_token"]
    assert data["refresh_token"] == tokens["refresh_token"]


def test_refresh_with_invalid_token_returns_401(auth_client):
    res = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not.a.valid.token"},
    )
    assert res.status_code == 401


def test_logout_revokes_refresh_token(auth_client):
    tokens = _login(auth_client)
    logout_res = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert logout_res.status_code == 204

    refresh_res = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh_res.status_code == 401


def test_multiple_devices_can_stay_logged_in(auth_client):
    device_a = "device-a-uuid"
    device_b = "device-b-uuid"

    tokens_a = _login_device(auth_client, device_a)
    tokens_b = _login_device(auth_client, device_b)

    # Both refresh tokens remain valid for their respective devices.
    refresh_a = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens_a["refresh_token"]},
        headers={"X-Device-Id": device_a},
    )
    refresh_b = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens_b["refresh_token"]},
        headers={"X-Device-Id": device_b},
    )
    assert refresh_a.status_code == 200, refresh_a.text
    assert refresh_b.status_code == 200, refresh_b.text


def test_legacy_access_token_sliding_refresh_still_works(auth_client):
    tokens = _login(auth_client)
    res = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["access_token"] != tokens["access_token"]
    assert "refresh_token" in data


def _login_device(client: TestClient, device_id: str) -> dict:
    res = client.post(
        "/api/v1/auth/token",
        json={"username": "admin", "password": "test"},
        headers={"X-Device-Id": device_id},
    )
    assert res.status_code == 200, res.text
    return res.json()
