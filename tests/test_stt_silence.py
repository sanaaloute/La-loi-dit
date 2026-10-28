"""Silence gate for /chat/transcribe: silent clips must never reach Whisper.

A silent (or noise-floor-only) upload makes the model hallucinate subtitle
credits — famously « Sous-titres réalisés par la communauté d'Amara.org » —
which then lands in the user's composer as if it were their dictation.
"""

import io
import wave

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core import stt
from backend.core.config import get_settings
from backend.core.exceptions import NoSpeechError, STTError


def _silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """In-memory WAV of digital silence (stdlib only, no fixture files)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


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


def test_silent_clip_rejected_before_model_load(monkeypatch, tmp_path):
    """The gate fires on the decoded PCM before the Whisper model is loaded."""
    pytest.importorskip("faster_whisper")
    monkeypatch.setenv("LEGAL_AI_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    def _fail_load(_settings):
        raise AssertionError("the model must not load for a silent clip")

    monkeypatch.setattr(stt, "_load_whisper_model", _fail_load)
    with pytest.raises(NoSpeechError):
        stt._transcribe_local(get_settings(), _silent_wav(), "vocal.wav")


def test_transcribe_route_maps_no_speech_to_422(chat_client, monkeypatch):
    async def _raise_no_speech(_audio, _filename):
        raise NoSpeechError("silent")

    monkeypatch.setattr(stt, "transcribe_audio", _raise_no_speech)
    res = chat_client.post(
        "/api/v1/chat/transcribe",
        files={"file": ("vocal.wav", _silent_wav(), "audio/wav")},
        headers={"Authorization": f"Bearer {_token(chat_client)}"},
    )
    assert res.status_code == 422, res.text
    assert "Aucune voix" in res.json()["detail"]


def test_transcribe_route_maps_provider_failure_to_502(chat_client, monkeypatch):
    async def _raise_stt(_audio, _filename):
        raise STTError("boom")

    monkeypatch.setattr(stt, "transcribe_audio", _raise_stt)
    res = chat_client.post(
        "/api/v1/chat/transcribe",
        files={"file": ("vocal.wav", _silent_wav(), "audio/wav")},
        headers={"Authorization": f"Bearer {_token(chat_client)}"},
    )
    assert res.status_code == 502, res.text
