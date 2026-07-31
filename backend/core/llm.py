"""Multi-provider LLM client built on LiteLLM.

Providers (OpenAI, Claude, Gemini, DeepSeek, Qwen, Kimi, local Ollama) are
selected purely through settings — agents never touch provider SDKs. The
`mock` provider is deterministic and offline: it powers tests and local dev,
and it makes the whole pipeline runnable with zero external credentials.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Type, TypeVar

import litellm
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.exceptions import LLMError
from backend.observability.langfuse_client import litellm_trace_metadata

T = TypeVar("T", bound=BaseModel)

litellm.drop_params = True

# Map a logical provider to a LiteLLM model prefix.
PROVIDER_PREFIXES = {
    "openai": "",
    "anthropic": "anthropic/",
    "gemini": "gemini/",
    "deepseek": "deepseek/",
    "qwen": "openai/",  # OpenAI-compatible endpoint
    "kimi": "openai/",  # OpenAI-compatible endpoint
    "ollama": "ollama/",
    "openrouter": "openrouter/",
    "tokenfree": "openai/",  # OpenAI-compatible endpoint
    "mock": "mock/",
}

OPENROUTER_DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
# TokenFree is an OpenAI-compatible API (api key + base_url, see
# https://www.tokenfree.ai). Their docs show http://api.tokenfree.ai/v1 —
# we default to https; override with LEGAL_AI_LLM_API_BASE if needed.
TOKENFREE_DEFAULT_API_BASE = "https://api.tokenfree.ai/v1"


class LLMClient:
    """Thin async wrapper with JSON-mode parsing and one corrective retry.

    Defaults are fully settings-driven (unchanged behavior). The optional
    `provider`/`model`/`api_key`/`api_base` overrides let the model router
    build a per-request client for one catalog model; `model` may then be a
    namespaced catalog id (``"openrouter/deepseek/deepseek-chat"``) — the
    leading provider namespace is stripped before the LiteLLM prefix is
    applied.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        self.settings = settings
        self.provider = (provider or settings.llm_provider).lower()
        raw_model = model if model is not None else settings.llm_model
        namespace = f"{self.provider}/"
        if raw_model.startswith(namespace):
            raw_model = raw_model[len(namespace):]
        prefix = PROVIDER_PREFIXES.get(self.provider, "")
        self.model = f"{prefix}{raw_model}"
        self.api_key = settings.llm_api_key if api_key is None else api_key
        if api_base is not None:
            self.api_base = api_base
        elif self.provider == settings.llm_provider.lower():
            self.api_base = settings.llm_api_base
        else:
            # The global base belongs to the default provider; it must not
            # leak into per-request clients for other providers.
            self.api_base = ""
        if self.provider == "openrouter" and not self.api_base:
            self.api_base = OPENROUTER_DEFAULT_API_BASE
        if self.provider == "tokenfree" and not self.api_base:
            self.api_base = TOKENFREE_DEFAULT_API_BASE
        # Cumulative token metering across calls on this instance. Read as a
        # delta around one request when the client is shared (mock mode).
        self.usage_totals: dict[str, int] = {"tokens_in": 0, "tokens_out": 0}

    # ------------------------------------------------------------------
    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Offline heuristic (~4 chars/token) used for mock/usage-less calls."""
        return max(1, len(text) // 4) if text else 0

    def _record_usage(self, system: str, user: str, completion: str, usage: Any) -> None:
        """Accumulate one call's tokens (provider usage when available)."""
        tokens_in = getattr(usage, "prompt_tokens", None)
        tokens_out = getattr(usage, "completion_tokens", None)
        if tokens_in is None and isinstance(usage, dict):
            tokens_in = usage.get("prompt_tokens")
            tokens_out = usage.get("completion_tokens")
        try:
            tokens_in = int(tokens_in)  # type: ignore[arg-type]
            tokens_out = int(tokens_out)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            tokens_in = tokens_out = None  # absent/mocked usage -> estimate
        if tokens_in is not None and tokens_out is not None:
            self.usage_totals["tokens_in"] += tokens_in
            self.usage_totals["tokens_out"] += tokens_out
        else:
            self.usage_totals["tokens_in"] += self._estimate_tokens(system) + self._estimate_tokens(user)
            self.usage_totals["tokens_out"] += self._estimate_tokens(completion)

    # ------------------------------------------------------------------
    async def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        if self.provider == "mock":
            result = self._mock_complete(system, user)
            self._record_usage(system, user, result, None)
            return result
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "timeout": self.settings.llm_timeout_seconds,
            "metadata": litellm_trace_metadata(),
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        extra_headers: dict[str, str] = {}
        # Ollama Cloud (and any authenticated Ollama endpoint) expects the
        # API key as a Bearer token. LiteLLM's ollama provider does not add
        # this header automatically, so we inject it via extra_headers.
        if self.provider == "ollama" and self.api_key:
            extra_headers["Authorization"] = f"Bearer {self.api_key}"
        # OpenRouter rankings/attribution headers (app name as fallback).
        if self.provider == "openrouter":
            extra_headers.setdefault("HTTP-Referer", self.settings.app_name)
            extra_headers.setdefault("X-Title", self.settings.app_name)
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        try:
            resp = await litellm.acompletion(**kwargs)
            content = resp.choices[0].message.content or ""
            self._record_usage(system, user, content, getattr(resp, "usage", None))
            return content
        except Exception as exc:  # provider/network errors
            raise LLMError(f"LLM call failed ({self.model}): {exc}") from exc

    async def complete_json(self, system: str, user: str, schema: Type[T]) -> T:
        """Ask for JSON and validate against `schema`; one corrective retry (max retry = 1)."""
        json_system = (
            f"{system}\n\nYou MUST answer with a single valid JSON object matching this "
            f"JSON Schema, no prose, no markdown fences:\n{json.dumps(schema.model_json_schema(), default=str)}"
        )
        raw = await self.complete(json_system, user)
        try:
            return schema.model_validate(self._extract_json(raw))
        except Exception:
            fix = f"Your previous answer was not valid JSON. Return ONLY the JSON object.\nPrevious answer:\n{raw}"
            raw2 = await self.complete(json_system, fix)
            try:
                return schema.model_validate(self._extract_json(raw2))
            except Exception as exc:
                raise LLMError(f"LLM returned invalid JSON for {schema.__name__}: {exc}") from exc

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(raw: str) -> Any:
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
        if start == -1:
            raise ValueError("no JSON found in LLM output")
        return json.loads(text[start:])

    # ------------------------------------------------------------------
    def _mock_complete(self, system: str, user: str) -> str:
        """Deterministic offline completions keyed off the system prompt.

        Lets the full graph run end-to-end in tests and demos without any
        API key, while never inventing legal content: the mock response
        generator only restates evidence it was actually given.
        """
        s = system.lower()
        top_k = self.settings.default_top_k
        if "retrieval plan" in s or "plan the searches" in s:
            return json.dumps(
                {
                    "sub_questions": [user[:200]],
                    "tasks": [
                        {"kind": "vector", "query": user[:200], "top_k": top_k, "filters": {}},
                        {"kind": "keyword", "query": user[:200], "top_k": top_k, "filters": {}},
                    ],
                    "legal_domains": [],
                    "retrieval_language": "fr",
                    "response_language": "fr",
                    "scenario_date": None,
                    "rationale": "mock planner: default hybrid retrieval",
                }
            )
        if "reflect" in s or "self-critique" in s:
            return json.dumps(
                {
                    "complete": True,
                    "answered_all_questions": True,
                    "all_claims_cited": True,
                    "contradictions_found": False,
                    "issues": [],
                    "should_retry_retrieval": False,
                    "retry_query": None,
                }
            )
        if "reason" in s:
            return "Analyse fondée exclusivement sur les extraits de preuve fournis."
        if "respond" in s or "answer" in s or "rédige" in s:
            return ""  # response generator falls back to template composition
        return json.dumps({}) if "json" in s else ""


def get_llm(settings: Settings) -> LLMClient:
    return LLMClient(settings)
