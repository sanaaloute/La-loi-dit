import json

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with a fresh app and a tmp data dir for the release file."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_REDIS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_MILVUS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEGAL_AI_SECRET_KEY", "test-secret-key-for-jwt-signing")
    monkeypatch.setenv("LEGAL_AI_DEV_USERS", "admin:test:admin")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/legal_ai.db")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as c:
        yield c, tmp_path


def test_app_version_is_public_and_reads_release_file(client):
    c, data_dir = client
    (data_dir / "mobile_release.json").write_text(
        json.dumps({"latest_version": "1.2.3", "release_notes": "Corrections de bogues."}),
        encoding="utf-8",
    )
    res = c.get("/api/v1/app/version")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["latest_version"] == "1.2.3"
    assert body["release_notes"] == "Corrections de bogues."
    assert body["store_url_ios"].endswith("6826883491")
    assert "net.neobytech.yawoto" in body["store_url_android"]


def test_app_version_without_release_file_reports_no_update(client):
    c, _ = client
    res = c.get("/api/v1/app/version")
    assert res.status_code == 200, res.text
    assert res.json()["latest_version"] == ""


def test_app_version_with_corrupt_file_reports_no_update(client):
    c, data_dir = client
    (data_dir / "mobile_release.json").write_text("not json", encoding="utf-8")
    res = c.get("/api/v1/app/version")
    assert res.status_code == 200, res.text
    assert res.json()["latest_version"] == ""
