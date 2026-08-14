"""Speech-to-text (STT) providers.

Provider selection is settings-driven (``stt_provider``), same convention as
core/llm.py and core/embeddings.py:

- ``litellm`` (default): transcription through the LiteLLM gateway
  (``litellm.atranscription`` — OpenAI Whisper or any compatible endpoint),
  reusing the main LLM credentials.
- ``faster-whisper``: fully local transcription. The package is heavy and
  import-guarded: when it is missing, ``stt_available()`` reports False (with
  one clear warning) and the rest of the platform runs unchanged. The loaded
  model is evicted after ``_WHISPER_IDLE_SECONDS`` without use so an idle
  backend does not keep hundreds of MB of RAM allocated.
"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import io
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import litellm

from backend.core.config import Settings, get_settings
from backend.core.exceptions import STTError

logger = logging.getLogger(__name__)

# Lazy singleton for the local provider: building WhisperModel is expensive
# (model download + load), so it happens once on first use. The loader runs
# in a worker thread (asyncio.to_thread), so the lock keeps concurrent first
# calls from building the model twice.
_WHISPER_MODEL: Any = None
_WHISPER_MODEL_LOCK = threading.Lock()
# Warn once (not per request) when the local provider is selected but its
# package is missing.
_WARNED_MISSING_FASTER_WHISPER = False

# Idle eviction: a loaded Whisper model holds hundreds of MB of RAM, so a
# daemon reaper unloads it after this long without a transcription. The next
# request simply reloads it (a few seconds of latency).
_WHISPER_IDLE_SECONDS = 60.0
_REAPER_CHECK_SECONDS = 10.0
_WHISPER_LAST_USED = 0.0  # monotonic timestamp of the last load/transcription
_WHISPER_REAPER_STARTED = False


def _faster_whisper_installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def stt_available() -> bool:
    """Whether the configured STT provider can serve a transcription."""
    global _WARNED_MISSING_FASTER_WHISPER
    provider = get_settings().stt_provider.lower()
    if provider == "litellm":
        return True
    if provider == "faster-whisper":
        if _faster_whisper_installed():
            return True
        if not _WARNED_MISSING_FASTER_WHISPER:
            _WARNED_MISSING_FASTER_WHISPER = True
            logger.warning(
                "stt_provider=faster-whisper but the 'faster-whisper' package is "
                "not installed; audio transcription is unavailable"
            )
        return False
    return False


def _evict_idle_whisper_model() -> bool:
    """Drop the cached Whisper model when it has been idle too long.

    In-flight transcriptions are safe: they hold their own reference to the
    model object, so dropping the global only prevents *new* uses of the
    stale instance — memory is freed once the last reference returns.
    """
    if _WHISPER_MODEL is None:
        return False
    if time.monotonic() - _WHISPER_LAST_USED < _WHISPER_IDLE_SECONDS:
        return False
    with _WHISPER_MODEL_LOCK:
        if _WHISPER_MODEL is None:
            return False
        if time.monotonic() - _WHISPER_LAST_USED < _WHISPER_IDLE_SECONDS:
            return False
        logger.info(
            "unloading faster-whisper model after %ds idle",
            int(_WHISPER_IDLE_SECONDS),
        )
        globals()["_WHISPER_MODEL"] = None
    gc.collect()
    return True


def _whisper_reaper_loop() -> None:
    """Periodically evict the local Whisper model once it goes idle."""
    while True:
        time.sleep(_REAPER_CHECK_SECONDS)
        _evict_idle_whisper_model()


def _ensure_reaper_started() -> None:
    global _WHISPER_REAPER_STARTED
    if _WHISPER_REAPER_STARTED:
        return
    with _WHISPER_MODEL_LOCK:
        if _WHISPER_REAPER_STARTED:
            return
        _WHISPER_REAPER_STARTED = True
    thread = threading.Thread(
        target=_whisper_reaper_loop, name="whisper-idle-reaper", daemon=True
    )
    thread.start()


def _load_whisper_model(settings: Settings) -> Any:
    """Return the cached local WhisperModel, building it on first call."""
    global _WHISPER_MODEL, _WHISPER_LAST_USED
    _WHISPER_LAST_USED = time.monotonic()
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    with _WHISPER_MODEL_LOCK:
        if _WHISPER_MODEL is not None:  # another thread built it while we waited
            return _WHISPER_MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError(
                "stt_provider=faster-whisper but the 'faster-whisper' package is not "
                "installed. Run: pip install faster-whisper"
            ) from exc
        models_dir = settings.stt_models_path
        models_dir.mkdir(parents=True, exist_ok=True)
        _WHISPER_MODEL = WhisperModel(
            settings.faster_whisper_model_size,
            device="cpu",
            compute_type="int8",
            download_root=str(models_dir),
        )
        _ensure_reaper_started()
        return _WHISPER_MODEL


def _transcribe_local(settings: Settings, audio_bytes: bytes, filename: str) -> str:
    """Blocking faster-whisper transcription (runs in a worker thread)."""
    global _WHISPER_LAST_USED
    model = _load_whisper_model(settings)
    # faster-whisper accepts a path (a file-like only in newer versions); a
    # temp file works across all versions and lets the decoder sniff the
    # container format from the extension. delete=False + the try/finally
    # below: the file is unlinked on every path once created.
    suffix = Path(filename).suffix or ".webm"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = Path(tmp.name)
    try:
        tmp.write(audio_bytes)
        tmp.close()
        segments, _info = model.transcribe(str(tmp_path), language=settings.stt_language)
        return "".join(segment.text for segment in segments).strip()
    finally:
        _WHISPER_LAST_USED = time.monotonic()
        tmp.close()
        tmp_path.unlink(missing_ok=True)


async def _transcribe_litellm(settings: Settings, audio_bytes: bytes, filename: str) -> str:
    # LiteLLM/OpenAI sniff the audio format from the file object's name, so
    # the BytesIO must carry the original filename (extension) for the
    # content-type detection to work.
    buffer = io.BytesIO(audio_bytes)
    buffer.name = filename  # type: ignore[attr-defined]
    kwargs: dict[str, Any] = {
        "model": settings.stt_model,
        "file": buffer,
        "language": settings.stt_language,
        "timeout": settings.stt_timeout_seconds,
    }
    # Dedicated transcription credentials win; otherwise reuse the main LLM
    # credentials (same convention as the embedding provider in
    # core/embeddings.py). NOTE: an Ollama chat setup cannot serve Whisper —
    # point stt_api_base at a transcription-capable endpoint or switch to
    # stt_provider=faster-whisper.
    api_key = settings.stt_api_key or settings.llm_api_key
    api_base = settings.stt_api_base or settings.llm_api_base
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    response = await litellm.atranscription(**kwargs)
    text = getattr(response, "text", None)
    if text is None and isinstance(response, dict):
        text = response.get("text")
    return (text or "").strip()


async def transcribe_audio(audio_bytes: bytes, filename: str) -> str:
    """Transcribe audio bytes to text with the configured provider.

    Raises STTError on provider failures or misconfiguration.
    """
    settings = get_settings()
    provider = settings.stt_provider.lower()
    try:
        if provider == "litellm":
            return await _transcribe_litellm(settings, audio_bytes, filename)
        if provider == "faster-whisper":
            # Local transcription is CPU-bound: keep the event loop free.
            return await asyncio.to_thread(_transcribe_local, settings, audio_bytes, filename)
    except STTError:
        raise
    except Exception as exc:  # provider/network/decoder errors
        logger.warning("audio transcription failed (%s): %s", provider, exc)
        raise STTError(f"audio transcription failed ({provider}): {exc}") from exc
    raise STTError(
        f"unknown stt_provider: {settings.stt_provider!r} (expected 'litellm' or 'faster-whisper')"
    )
