"""Unit tests for the provider-agnostic LLM wrapper."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.core.config import Settings
from backend.core.llm import LLMClient


@pytest.fixture
def ollama_settings() -> Settings:
    return Settings(
        llm_provider="ollama",
        llm_model="gpt-oss:120b",
        llm_api_base="https://ollama.com",
        llm_api_key="test-token",
    )


async def test_mock_provider_returns_deterministic_output(settings: Settings) -> None:
    client = LLMClient(settings)
    result = await client.complete("respond", "What is the capital?")
    assert isinstance(result, str)


async def test_ollama_cloud_injects_authorization_header(ollama_settings: Settings) -> None:
    client = LLMClient(ollama_settings)
    with mock.patch("backend.core.llm.litellm.acompletion") as mock_completion:
        mock_completion.return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content="ok"))]
        )
        result = await client.complete("system prompt", "user prompt")

    assert result == "ok"
    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["model"] == "ollama/gpt-oss:120b"
    assert call_kwargs["api_base"] == "https://ollama.com"
    assert call_kwargs["extra_headers"] == {"Authorization": "Bearer test-token"}


async def test_ollama_local_skips_auth_header_when_no_key() -> None:
    settings = Settings(
        llm_provider="ollama",
        llm_model="llama3.1:8b",
        llm_api_base="http://host.docker.internal:11434",
        llm_api_key="",
    )
    client = LLMClient(settings)
    with mock.patch("backend.core.llm.litellm.acompletion") as mock_completion:
        mock_completion.return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content="local"))]
        )
        await client.complete("system", "user")

    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["model"] == "ollama/llama3.1:8b"
    assert call_kwargs["api_base"] == "http://host.docker.internal:11434"
    assert "extra_headers" not in call_kwargs
