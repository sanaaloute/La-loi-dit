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


async def test_ollama_cloud_uses_native_client(ollama_settings: Settings) -> None:
    client = LLMClient(ollama_settings)
    mock_response = mock.Mock()
    mock_response.message.content = "ok"
    with mock.patch("backend.core.llm.litellm.acompletion") as mock_litellm, \
         mock.patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.chat.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = await client.complete("system prompt", "user prompt")

    assert result == "ok"
    # LiteLLM path must not be used for ollama.com.
    mock_litellm.assert_not_called()
    # Native ollama client must be configured with Bearer auth and bare model name.
    mock_client_cls.assert_called_once_with(
        host="https://ollama.com",
        headers={"Authorization": "Bearer test-token"},
    )
    call_kwargs = mock_client.chat.call_args.kwargs
    assert call_kwargs["model"] == "gpt-oss:120b"
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]
    assert call_kwargs["stream"] is False


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


class _StubClient:
    """Minimal LLMClient stand-in for failover tests."""

    def __init__(self, model: str, behavior):
        self.model = model
        self.provider = model
        self.api_key = "k"
        self.api_base = "b"
        self._behavior = behavior
        self.calls = 0
        self.usage_totals = {"tokens_in": 0, "tokens_out": 0}

    async def complete(self, system, user, *, temperature=None, max_tokens=None):
        self.calls += 1
        if self._behavior == "raise":
            raise RuntimeError("provider down")
        return self._behavior


async def test_failover_skips_failing_provider() -> None:
    from backend.core.llm import FailoverLLMClient

    chain = FailoverLLMClient([_StubClient("a", "raise"), _StubClient("b", "réponse [1]")])
    assert await chain.complete("sys", "user") == "réponse [1]"


async def test_failover_skips_empty_completion() -> None:
    from backend.core.llm import FailoverLLMClient

    chain = FailoverLLMClient([_StubClient("a", ""), _StubClient("b", "réponse [1]")])
    assert await chain.complete("sys", "user") == "réponse [1]"


async def test_failover_raises_when_all_providers_fail() -> None:
    from backend.core.exceptions import LLMError
    from backend.core.llm import FailoverLLMClient

    chain = FailoverLLMClient([_StubClient("a", "raise"), _StubClient("b", "raise")])
    with pytest.raises(LLMError, match="all LLM providers failed"):
        await chain.complete("sys", "user")


def test_with_failover_builds_chain_from_configured_keys() -> None:
    from backend.core.llm import FailoverLLMClient, LLMClient
    from backend.core.model_router import with_failover

    settings = Settings(
        llm_provider="ollama",
        llm_model="gpt-oss:120b",
        llm_api_key="sk-ollama",
        openrouter_api_key="sk-or",
    )
    client = with_failover(LLMClient(settings), settings)
    assert isinstance(client, FailoverLLMClient)
    assert client.provider == "ollama"  # primary first
    assert client.clients[1].provider == "openrouter"
    assert client.clients[1].model == "openrouter/openai/gpt-oss-20b:free"
    # tokenfree has no dedicated key -> falls back to the main llm_api_key
    assert client.clients[2].provider == "tokenfree"
    assert client.clients[2].api_key == "sk-ollama"


def test_with_failover_noop_without_keys_or_mock() -> None:
    from backend.core.llm import FailoverLLMClient, LLMClient
    from backend.core.model_router import with_failover

    mock_client = LLMClient(Settings(llm_provider="mock"))
    assert with_failover(mock_client, Settings(llm_provider="mock")) is mock_client

    no_keys = Settings(llm_provider="openai", llm_fallback_providers="openrouter")
    plain = LLMClient(no_keys)
    assert with_failover(plain, no_keys) is plain


async def test_mock_routes_response_system_prompt_to_grounded_answer(settings: Settings) -> None:
    """Regression: RESPONSE_SYSTEM must not collide with earlier mock branches.

    The mock router dispatches on system-prompt substrings ("reason" for the
    reasoning agent, "reflect" for reflection, ...) checked BEFORE the
    respond/answer branch. A response-generator prompt containing such a
    substring silently gets the wrong canned completion — the answer then has
    no [n] markers and every evaluation case collapses to the unavailability
    fallback.
    """
    from backend.core.prompts import get_prompt

    client = LLMClient(settings)
    user = 'Question: q ?\n\nPreuves:\n[1] Code du travail, art. 95: Le préavis est d\'un mois.'
    for name in ("RESPONSE_SYSTEM", "RESPONSE_SECTIONS_ADDENDUM_FR", "RESPONSE_CASE_ANALYSIS_ADDENDUM_FR"):
        system = get_prompt("RESPONSE_SYSTEM") + ("" if name == "RESPONSE_SYSTEM" else get_prompt(name))
        result = await client.complete(system, user)
        assert "[1]" in result, f"mock routed {name} away from the grounded answer"
