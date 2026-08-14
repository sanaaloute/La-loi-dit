"""Speech-to-text tests: POST /chat/transcribe endpoint and provider dispatch.

Fully offline (mock LLM, tmp SQLite). STT providers are mocked — no model
download, no network.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

os.environ["LEGAL_AI_LLM_PROVIDER"] = "mock"
os.environ["LEGAL_AI_LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LEGAL_AI_LANGFUSE_SECRET_KEY"] = ""

from backend.core import stt  # noqa: E402
from backend.core.config import Settings, get_settings  # noqa: E402
from backend.core.exceptions import STTError  # noqa: E402

PASSWORD = "motdepasse1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app + TestClient with a tmp user database (offline, dev mode)."""
    monkeypatch.setenv("LEGAL_AI_ENV", "development")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/stt_test.db")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from backend.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def strict_client(tmp_path, monkeypatch):
    """Non-development env: a missing bearer token is a hard 401."""
    monkeypatch.setenv("LEGAL_AI_ENV", "staging")
    monkeypatch.setenv("LEGAL_AI_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LEGAL_AI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/stt_strict.db")
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


def _audio_file(name: str = "vocal.webm", content_type: str = "audio/webm", payload: bytes = b"fake-audio"):
    return {"file": (name, payload, content_type)}


# ---------------------------------------------------------------------------
# POST /api/v1/chat/transcribe
# ---------------------------------------------------------------------------


def test_transcribe_requires_auth(strict_client):
    response = strict_client.post("/api/v1/chat/transcribe", files=_audio_file())
    assert response.status_code == 401


def test_transcribe_rejects_wrong_content_type(client):
    response = client.post(
        "/api/v1/chat/transcribe",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert "non pris en charge" in response.json()["detail"]


def test_transcribe_rejects_oversize_audio(client):
    client.app.state.ctx.settings.stt_max_audio_bytes = 8
    response = client.post("/api/v1/chat/transcribe", files=_audio_file(payload=b"x" * 16))
    assert response.status_code == 413
    assert "taille maximale" in response.json()["detail"]


def test_transcribe_success_returns_text(client, monkeypatch):
    async def fake_transcribe(audio_bytes: bytes, filename: str) -> str:
        assert audio_bytes == b"fake-audio"
        assert filename == "vocal.webm"
        return "bonjour le droit"

    monkeypatch.setattr(stt, "transcribe_audio", fake_transcribe)
    response = client.post("/api/v1/chat/transcribe", files=_audio_file())
    assert response.status_code == 200
    assert response.json() == {"text": "bonjour le droit"}


def test_transcribe_provider_error_returns_502(client, monkeypatch):
    async def boom(audio_bytes: bytes, filename: str) -> str:
        raise STTError("provider down")

    monkeypatch.setattr(stt, "transcribe_audio", boom)
    response = client.post("/api/v1/chat/transcribe", files=_audio_file())
    assert response.status_code == 502
    assert "transcription" in response.json()["detail"].lower()


def test_transcribe_unavailable_returns_503(client, monkeypatch):
    monkeypatch.setattr(stt, "stt_available", lambda: False)
    response = client.post("/api/v1/chat/transcribe", files=_audio_file())
    assert response.status_code == 503
    assert "pas disponible" in response.json()["detail"]


def test_transcribe_records_one_request(client, monkeypatch):
    _, token = _register(client)

    async def fake_transcribe(audio_bytes: bytes, filename: str) -> str:
        return "question dictée"

    monkeypatch.setattr(stt, "transcribe_audio", fake_transcribe)
    response = client.post("/api/v1/chat/transcribe", files=_audio_file(), headers=_headers(token))
    assert response.status_code == 200

    usage = client.get("/api/v1/usage/me", headers=_headers(token)).json()
    assert usage["today"]["requests"] == 1
    assert usage["today"]["tokens_in"] == 0
    assert usage["today"]["tokens_out"] == 0


def test_transcribe_over_budget_returns_429(client, monkeypatch):
    """The transcribe endpoint enforces the same daily budget as /chat."""
    import asyncio

    from backend.core import catalog
    from backend.security.jwt import decode_access_token

    _, token = _register(client)
    user_id = decode_access_token(token, get_settings()).user_id
    store = client.app.state.ctx.user_store
    asyncio.run(store.record_usage(user_id, 0, 0))  # one request already used today

    called: list[str] = []

    async def fake_transcribe(audio_bytes: bytes, filename: str) -> str:
        called.append(filename)
        return "ne doit pas être appelé"

    monkeypatch.setattr(stt, "transcribe_audio", fake_transcribe)
    catalog.set_budget_overrides({"gratuit": {"daily_request_budget": 1}})
    try:
        response = client.post(
            "/api/v1/chat/transcribe", files=_audio_file(), headers=_headers(token)
        )
        assert response.status_code == 429
        assert called == []  # no free transcription
    finally:
        catalog.set_budget_overrides({})


# ---------------------------------------------------------------------------
# transcribe_audio provider dispatch (unit)
# ---------------------------------------------------------------------------


def _patch_settings(monkeypatch, **overrides) -> Settings:
    """Point backend.core.stt at a hermetic Settings instance."""
    settings = Settings(**overrides)
    monkeypatch.setattr(stt, "get_settings", lambda: settings)
    return settings


async def test_litellm_dispatch_calls_atranscription(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="litellm")
    captured: dict = {}

    class _Response:
        text = "  transcription litellm  "

    async def fake_atranscription(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(stt.litellm, "atranscription", fake_atranscription)
    text = await stt.transcribe_audio(b"audio-bytes", "question.webm")
    assert text == "transcription litellm"
    assert captured["model"] == "whisper-1"
    assert captured["language"] == "fr"
    # The file-like carries the original filename (format sniffing).
    assert captured["file"].name == "question.webm"


async def test_litellm_error_wrapped_in_stt_error(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="litellm")

    async def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(stt.litellm, "atranscription", boom)
    with pytest.raises(STTError, match="network down"):
        await stt.transcribe_audio(b"audio-bytes", "vocal.webm")


async def test_faster_whisper_dispatch_uses_lazy_model(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="faster-whisper", stt_language="fr")
    calls: list = []

    class _Segment:
        def __init__(self, text: str):
            self.text = text

    class _FakeModel:
        def transcribe(self, path, language=None):
            calls.append((path, language))
            assert os.path.exists(path)  # a real temp file path is passed
            return [_Segment("bonjour "), _Segment("le droit")], None

    monkeypatch.setattr(stt, "_load_whisper_model", lambda settings: _FakeModel())
    text = await stt.transcribe_audio(b"audio-bytes", "vocal.ogg")
    assert text == "bonjour le droit"
    assert calls and calls[0][1] == "fr"
    assert calls[0][0].endswith(".ogg")  # extension preserved for decoding
    assert not os.path.exists(calls[0][0])  # temp file cleaned up


async def test_faster_whisper_missing_package_raises_stt_error(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="faster-whisper")
    monkeypatch.setattr(stt, "_WHISPER_MODEL", None)
    # A None sys.modules entry makes the guarded import raise ImportError.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(STTError, match="faster-whisper"):
        await stt.transcribe_audio(b"audio", "vocal.webm")


async def test_unknown_provider_raises_clear_error(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="bogus")
    with pytest.raises(STTError, match="unknown stt_provider"):
        await stt.transcribe_audio(b"audio", "vocal.webm")


def test_stt_available_litellm(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="litellm")
    assert stt.stt_available() is True


def test_stt_available_faster_whisper_missing_package_warns_once(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="faster-whisper")
    monkeypatch.setattr(stt, "_faster_whisper_installed", lambda: False)
    monkeypatch.setattr(stt, "_WARNED_MISSING_FASTER_WHISPER", False)
    assert stt.stt_available() is False
    assert stt._WARNED_MISSING_FASTER_WHISPER is True


def test_stt_available_unknown_provider(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="bogus")
    assert stt.stt_available() is False
