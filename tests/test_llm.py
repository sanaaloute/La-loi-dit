"""Unit tests for the local Ollama and deterministic mock chat wrapper."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.core.config import Settings
from backend.core.llm import SYNTHESIS_MODEL, LLMClient, get_llm


@pytest.fixture
def ollama_settings() -> Settings:
    return Settings(
        llm_provider="ollama",
        llm_model="legacy-model",
        llm_api_base="http://host.docker.internal:11434",
        llm_api_key="",
    )


async def test_mock_provider_returns_deterministic_output(settings: Settings) -> None:
    client = LLMClient(settings)
    result = await client.complete("respond", "What is the capital?")
    assert isinstance(result, str)


def test_external_chat_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported chat provider"):
        LLMClient(Settings(llm_provider="openrouter"))


def test_default_chat_model_is_fixed(ollama_settings: Settings) -> None:
    client = get_llm(ollama_settings)
    assert client.model == f"ollama/{SYNTHESIS_MODEL}"
    assert client.api_key == ""


async def test_ollama_local_skips_auth_header_when_no_key(ollama_settings: Settings) -> None:
    client = LLMClient(ollama_settings, model="legacy-model")
    with mock.patch("backend.core.llm.litellm.acompletion") as mock_completion:
        mock_completion.return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content="local"))]
        )
        result = await client.complete("system", "user")

    assert result == "local"
    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["model"] == "ollama/legacy-model"
    assert call_kwargs["api_base"] == "http://host.docker.internal:11434"
    assert "extra_headers" not in call_kwargs
    assert call_kwargs["think"] is False


async def test_ollama_tools_use_native_local_client(ollama_settings: Settings) -> None:
    client = LLMClient(ollama_settings, model="legacy-model")
    function = mock.Mock()
    function.name = "search"
    function.arguments = {"query": "SARL"}
    tool_call = mock.Mock(function=function)
    response = mock.Mock()
    response.message.content = ""
    response.message.tool_calls = [tool_call]
    with mock.patch("ollama.AsyncClient") as mock_client_cls:
        native = mock.AsyncMock()
        native.chat.return_value = response
        mock_client_cls.return_value = native
        result = await client.complete_tools(
            [{"role": "user", "content": "Recherche les règles SARL"}],
            [{"type": "function", "function": {"name": "search", "parameters": {}}}],
        )

    assert result.tool_calls == [{"name": "search", "arguments": {"query": "SARL"}}]
    mock_client_cls.assert_called_once_with(host="http://host.docker.internal:11434", headers={})
    call_kwargs = native.chat.call_args.kwargs
    assert call_kwargs["model"] == "legacy-model"
    assert call_kwargs["keep_alive"] == ollama_settings.ollama_keep_alive
    assert call_kwargs["think"] is False


async def test_mock_routes_response_system_prompt_to_grounded_answer(settings: Settings) -> None:
    from backend.core.prompts import get_prompt

    client = LLMClient(settings)
    user = 'Question: q ?\n\nPreuves:\n[1] Code du travail, art. 95: Le préavis est d\'un mois.'
    for name in ("RESPONSE_SYSTEM", "RESPONSE_SECTIONS_ADDENDUM_FR", "RESPONSE_CASE_ANALYSIS_ADDENDUM_FR"):
        system = get_prompt("RESPONSE_SYSTEM") + ("" if name == "RESPONSE_SYSTEM" else get_prompt(name))
        result = await client.complete(system, user)
        assert "[1]" in result, f"mock routed {name} away from the grounded answer"
