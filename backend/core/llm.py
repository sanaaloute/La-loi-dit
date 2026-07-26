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
    "mock": "mock/",
}


class LLMClient:
    """Thin async wrapper with JSON-mode parsing and one corrective retry."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.llm_provider.lower()
        prefix = PROVIDER_PREFIXES.get(self.provider, "")
        self.model = f"{prefix}{settings.llm_model}"

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
            return self._mock_complete(system, user)
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
        if self.settings.llm_api_key:
            kwargs["api_key"] = self.settings.llm_api_key
        if self.settings.llm_api_base:
            kwargs["api_base"] = self.settings.llm_api_base
        try:
            resp = await litellm.acompletion(**kwargs)
            return resp.choices[0].message.content or ""
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
