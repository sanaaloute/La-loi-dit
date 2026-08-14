"""Multi-provider LLM client built on LiteLLM.

Providers (OpenAI, Claude, Gemini, DeepSeek, Qwen, Kimi, local Ollama,
Ollama Cloud) are selected purely through settings — agents never touch
provider SDKs. The `mock` provider is deterministic and offline: it powers
tests and local dev, and it makes the whole pipeline runnable with zero
external credentials.
"""

from __future__ import annotations

import asyncio
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
    "ollama_cloud": "",  # native ollama client, model name passed through
    "openrouter": "openrouter/",
    "tokenfree": "openai/",  # OpenAI-compatible endpoint
    "mock": "mock/",
}

OPENROUTER_DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
# TokenFree is an OpenAI-compatible API (Bearer key + /v1/chat/completions,
# see https://www.tokenfree.com). Override with LEGAL_AI_LLM_API_BASE if needed.
TOKENFREE_DEFAULT_API_BASE = "https://www.tokenfree.com/v1"
OLLAMA_CLOUD_DEFAULT_API_BASE = "https://ollama.com"

# Default API base per provider — the "LLM provider config". The .env only
# needs to hold API keys: the base URL of each provider lives here, and the
# model actually used comes from the user's UI selection (per-request override
# via the model router), not from a hardcoded env model.
PROVIDER_DEFAULT_API_BASES = {
    "openrouter": OPENROUTER_DEFAULT_API_BASE,
    "tokenfree": TOKENFREE_DEFAULT_API_BASE,
    "ollama": OLLAMA_CLOUD_DEFAULT_API_BASE,
}


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
        if not self.api_base:
            # Provider-config default base (see PROVIDER_DEFAULT_API_BASES).
            self.api_base = PROVIDER_DEFAULT_API_BASES.get(self.provider, "")
        if self.provider == "ollama" and not self.api_base and self.api_key:
            # An API key with no configured base means Ollama Cloud (a local
            # Ollama has no key and keeps the litellm localhost default).
            self.api_base = OLLAMA_CLOUD_DEFAULT_API_BASE
        # Cumulative token metering across calls on this instance. Read as a
        # delta around one request when the client is shared (mock mode).
        self.usage_totals: dict[str, int] = {"tokens_in": 0, "tokens_out": 0}

    @property
    def _use_ollama_cloud_native(self) -> bool:
        """Use the native ollama SDK for Ollama Cloud or authenticated ollama.com.

        LiteLLM's ollama provider targets the native /api/generate endpoint but
        does not handle Ollama Cloud authentication correctly; the official
        `ollama` client does.
        """
        return self.provider in ("ollama_cloud",) or (
            self.provider == "ollama"
            and self.api_base
            and "ollama.com" in self.api_base
        )

    async def _ollama_cloud_complete(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Native async ollama client path for Ollama Cloud."""
        try:
            from ollama import AsyncClient
        except ImportError as exc:
            raise LLMError(
                "Ollama Cloud requested but the 'ollama' package is not installed. "
                "Run: pip install ollama"
            ) from exc

        host = self.api_base or "https://ollama.com"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = AsyncClient(host=host, headers=headers)
        # The native ollama client expects a bare model name (e.g. gpt-oss:120b),
        # not the LiteLLM-prefixed name (ollama/...).
        model = self.model
        if model.startswith("ollama/"):
            model = model[len("ollama/"):]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = await asyncio.wait_for(
                client.chat(
                    model=model,
                    messages=messages,
                    stream=False,
                    options={
                        "temperature": self.settings.llm_temperature if temperature is None else temperature,
                        "num_predict": max_tokens or self.settings.llm_max_tokens,
                    },
                ),
                timeout=self.settings.llm_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(
                f"Ollama Cloud call timed out after {self.settings.llm_timeout_seconds}s ({self.model})"
            ) from exc
        except Exception as exc:
            raise LLMError(f"Ollama Cloud call failed ({self.model}): {exc}") from exc
        content = response.message.content or ""
        self._record_usage(system, user, content, None)
        return content

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
        if self._use_ollama_cloud_native:
            return await self._ollama_cloud_complete(system, user, temperature, max_tokens)
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
        # HTTP headers must be latin-1; strip any non-ASCII characters
        # (e.g. an em-dash in the configured app name) or every call fails.
        if self.provider == "openrouter":
            ascii_title = self.settings.app_name.encode("ascii", "ignore").decode().strip() or "Legal AI"
            extra_headers.setdefault("HTTP-Referer", ascii_title)
            extra_headers.setdefault("X-Title", ascii_title)
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
    # Tool-calling (function calling) support
    # ------------------------------------------------------------------

    class ToolCallResponse:
        """Result of a single completion: either final text or requested tool calls."""

        def __init__(
            self,
            final_text: Optional[str] = None,
            tool_calls: Optional[list[dict[str, Any]]] = None,
            raw: str = "",
        ):
            self.final_text = final_text
            self.tool_calls = tool_calls or []
            self.raw = raw

    async def complete_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> "LLMClient.ToolCallResponse":
        """Ask the LLM to either use a tool or return final text.

        The default implementation uses a manual JSON fallback that works with
        any model/provider (including Ollama Cloud models that do not support
        native tool-calling).  Native tool-calling is used when the provider
        exposes it cleanly.
        """
        if self.provider == "mock":
            return self._mock_complete_tools(messages, tools)

        # Try native Ollama tools when the model is served by Ollama/Ollama Cloud.
        if self._use_ollama_cloud_native and tools:
            try:
                return await self._ollama_tools(messages, tools, temperature, max_tokens)
            except Exception:
                pass  # fall back to manual JSON mode

        # Try LiteLLM tool-calling for OpenAI-compatible providers.
        if not self._use_ollama_cloud_native and tools:
            try:
                return await self._litellm_tools(messages, tools, temperature, max_tokens)
            except Exception:
                pass  # fall back to manual JSON mode

        return await self._manual_tool_completion(messages, tools, temperature, max_tokens)

    def _build_manual_tool_prompt(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> tuple[str, str]:
        """Return (system, user) for a manual JSON tool-calling prompt."""
        system_lines = [
            "You are a helpful assistant with access to tools.  You must decide whether to use a tool or answer directly.",
            "When you use a tool, output ONLY a JSON object: {\"tool_calls\": [{\"name\": \"...\", \"arguments\": {...}}]}.",
            "When you have enough information to answer, output ONLY a JSON object: {\"final_text\": \"...\"}.",
            "Do not include any prose, markdown fences or explanation outside the JSON.",
            "\nAvailable tools:\n",
        ]
        for tool in tools:
            fn = tool.get("function", tool)
            system_lines.append(f"- {fn.get('name')}: {fn.get('description')}")
            system_lines.append(f"  parameters: {json.dumps(fn.get('parameters', {}), ensure_ascii=False)}")
        system = "\n".join(system_lines)
        user_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                continue
            user_parts.append(f"[{role}] {content}")
        return system, "\n\n".join(user_parts)

    async def _manual_tool_completion(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> "LLMClient.ToolCallResponse":
        system, user = self._build_manual_tool_prompt(messages, tools)
        raw = await self.complete(system, user, temperature=temperature, max_tokens=max_tokens)
        return self._parse_tool_response(raw)

    def _parse_tool_response(self, raw: str) -> "LLMClient.ToolCallResponse":
        """Parse a JSON response that may contain tool_calls or final_text."""
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
        if start == -1:
            # No JSON found; treat the whole response as final text.
            return self.ToolCallResponse(final_text=raw, raw=raw)
        try:
            payload = json.loads(text[start:])
        except Exception:
            return self.ToolCallResponse(final_text=raw, raw=raw)

        if isinstance(payload, dict):
            if "final_text" in payload and payload["final_text"] is not None:
                return self.ToolCallResponse(final_text=str(payload["final_text"]), raw=raw)
            if "tool_calls" in payload:
                return self.ToolCallResponse(tool_calls=list(payload["tool_calls"]), raw=raw)
        # If the payload is a bare list of tool calls, accept it too.
        if isinstance(payload, list):
            return self.ToolCallResponse(tool_calls=payload, raw=raw)
        return self.ToolCallResponse(final_text=raw, raw=raw)

    async def _ollama_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> "LLMClient.ToolCallResponse":
        from ollama import AsyncClient

        host = self.api_base or "https://ollama.com"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = AsyncClient(host=host, headers=headers)
        model = self.model
        if model.startswith("ollama/"):
            model = model[len("ollama/"):]
        try:
            response = await asyncio.wait_for(
                client.chat(
                    model=model,
                    messages=messages,
                    tools=tools,
                    stream=False,
                    options={
                        "temperature": self.settings.llm_temperature if temperature is None else temperature,
                        "num_predict": max_tokens or self.settings.llm_max_tokens,
                    },
                ),
                timeout=self.settings.llm_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(
                f"Ollama Cloud tool call timed out after {self.settings.llm_timeout_seconds}s ({self.model})"
            ) from exc
        except Exception as exc:
            raise LLMError(f"Ollama Cloud tool call failed ({self.model}): {exc}") from exc
        message = response.message
        if getattr(message, "tool_calls", None):
            calls = []
            for tc in message.tool_calls:
                calls.append({"name": tc.function.name, "arguments": tc.function.arguments})
            return self.ToolCallResponse(tool_calls=calls, raw=str(message))
        content = message.content or ""
        return self.ToolCallResponse(final_text=content, raw=content)

    async def _litellm_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> "LLMClient.ToolCallResponse":
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
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
        if self.provider == "ollama" and self.api_key:
            extra_headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider == "openrouter":
            extra_headers.setdefault("HTTP-Referer", self.settings.app_name)
            extra_headers.setdefault("X-Title", self.settings.app_name)
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        resp = await litellm.acompletion(**kwargs)
        choice = resp.choices[0]
        message = choice.message
        if getattr(message, "tool_calls", None):
            calls = []
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                calls.append({"name": tc.function.name, "arguments": args})
            return self.ToolCallResponse(tool_calls=calls, raw=str(message))
        content = message.content or ""
        return self.ToolCallResponse(final_text=content, raw=content)

    def _mock_complete_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
    ) -> "LLMClient.ToolCallResponse":
        """Mock tool-calling: infer intent from the last user message and system.

        The planner is bounded: if the conversation already contains tool results,
        the mock produces a final plan JSON instead of calling tools forever.
        """
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        s = system.lower()

        # Detect whether tool results have already been observed in the loop.
        has_tool_results = any(
            "tool results" in (m.get("content", "").lower())
            or m.get("role") == "tool"
            for m in messages
        )

        if "retrieval plan" in s or "plan" in s or "build_search_tasks" in s:
            if has_tool_results:
                # Produce a final structured plan after the tools have run.
                return self.ToolCallResponse(
                    final_text=json.dumps(
                        {
                            "sub_questions": [last_user[:200]],
                            "tasks": [
                                {"kind": "vector", "query": last_user[:200], "top_k": 8, "filters": {}},
                                {"kind": "keyword", "query": last_user[:200], "top_k": 8, "filters": {}},
                            ],
                            "legal_domains": [],
                            "retrieval_language": "fr",
                            "response_language": "fr",
                            "scenario_date": None,
                            "rationale": "mock planner: final plan after tools",
                        }
                    )
                )
            return self.ToolCallResponse(
                tool_calls=[{"name": "build_search_tasks", "arguments": {"query": last_user[:200], "top_k": 8}}],
            )
        if "reason" in s or "analyze" in s:
            return self.ToolCallResponse(
                final_text="Analyse fondée exclusivement sur les extraits de preuve fournis.",
            )
        if "reflect" in s or "self-critique" in s:
            return self.ToolCallResponse(
                final_text=json.dumps(
                    {
                        "complete": True,
                        "answered_all_questions": True,
                        "all_claims_cited": True,
                        "contradictions_found": False,
                        "issues": [],
                        "should_retry_retrieval": False,
                        "retry_query": None,
                    }
                ),
            )
        if "respond" in s or "answer" in s or "rédige" in s or "draft" in s:
            return self.ToolCallResponse(final_text="")
        # Default: if there are tools, call the first plausible one; otherwise final empty.
        if tools:
            first = tools[0].get("function", tools[0]).get("name", "")
            return self.ToolCallResponse(tool_calls=[{"name": first, "arguments": {}}])
        return self.ToolCallResponse(final_text="")

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
        if "conversation directe" in s:
            # Direct-route conversational answer (query router short-circuit,
            # RESPONSE_DIRECT_SYSTEM): a canned polite reply keeps offline
            # runs deterministic without inventing legal content.
            return (
                "Bonjour ! Je suis votre assistant de recherche juridique pour le "
                "Burkina Faso. Posez-moi une question juridique et je chercherai la "
                "réponse dans les sources officielles indexées (Constitution, codes, "
                "lois, Journal Officiel, OHADA). Mes réponses sont fournies à titre "
                "informatif et ne constituent pas un avis juridique."
            )
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
            # A minimal citation-grounded answer, so tests exercise the real
            # LLM-synthesis path — there is no template fallback anymore.
            return self._mock_grounded_answer(user)
        return json.dumps({}) if "json" in s else ""

    #: Cap on each quoted excerpt in the mock answer (excerpts can be long).
    _MOCK_QUOTE_CHARS = 1500

    def _mock_grounded_answer(self, user: str) -> str:
        """Quote each numbered evidence excerpt with its [n] citation marker.

        The mock never invents legal content: it restates only what the prompt
        actually provided, which keeps the offline pipeline deterministic while
        letting answer-level evaluation metrics (keyword relevance, issue
        coverage) see the same legal terms a real synthesis would carry.
        Returns "" when the prompt contains no numbered excerpt.
        """
        if "[1]" not in user:
            return ""
        lines = [
            "Sur la base des preuves fournies, la réponse à la question est "
            "établie comme suit [1]."
        ]
        # Excerpts are "[n] label: content" lines, the first prefixed by the
        # "Preuves:" header — anchor on the numbered line starts instead of
        # splitting blocks so the header cannot swallow excerpt [1].
        entries = re.findall(
            r"(?ms)^\[(\d+)\][^\n]*?:\s*(.+?)(?=^\[\d+\]|\Z)", user
        )
        for index, content in entries:
            quote = " ".join(content.split())
            lines.append(f"- {quote[: self._MOCK_QUOTE_CHARS]} [{index}]")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)


def get_llm(settings: Settings) -> LLMClient:
    return LLMClient(settings)


class FailoverLLMClient:
    """Chain of provider clients: the first one that answers wins.

    A completion that raises OR returns empty text fails over to the next
    provider, so a single broken provider/model never degrades the answer to
    a non-LLM fallback. Built only with providers that have credentials —
    see ``backend.core.model_router.with_failover``.
    """

    def __init__(self, clients: list[LLMClient]):
        if not clients:
            raise ValueError("FailoverLLMClient needs at least one client")
        self.clients = clients

    # -- surface area expected by callers (metering, logging, tracing) ------
    @property
    def primary(self) -> LLMClient:
        return self.clients[0]

    @property
    def provider(self) -> str:
        return self.primary.provider

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def api_key(self) -> str:
        return self.primary.api_key

    @property
    def api_base(self) -> str:
        return self.primary.api_base

    @property
    def usage_totals(self) -> dict[str, int]:
        return {
            "tokens_in": sum(c.usage_totals["tokens_in"] for c in self.clients),
            "tokens_out": sum(c.usage_totals["tokens_out"] for c in self.clients),
        }

    # -- completions ---------------------------------------------------------
    async def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        last_exc: Optional[Exception] = None
        for client in self.clients:
            try:
                text = await client.complete(system, user, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                last_exc = exc
                continue
            if text.strip():
                return text
        if last_exc is not None:
            raise LLMError(
                f"all LLM providers failed ({', '.join(c.model for c in self.clients)}): {last_exc}"
            )
        return ""  # every provider returned an empty completion

    async def complete_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> "LLMClient.ToolCallResponse":
        last_exc: Optional[Exception] = None
        for client in self.clients:
            try:
                return await client.complete_tools(messages, tools, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                last_exc = exc
        raise LLMError(
            f"all LLM providers failed tool calling ({', '.join(c.model for c in self.clients)}): {last_exc}"
        )

    async def complete_json(self, system: str, user: str, schema: Type[T]) -> T:
        """JSON-mode with one corrective retry, over the failover chain."""
        json_system = (
            f"{system}\n\nYou MUST answer with a single valid JSON object matching this "
            f"JSON Schema, no prose, no markdown fences:\n{json.dumps(schema.model_json_schema(), default=str)}"
        )
        raw = await self.complete(json_system, user)
        try:
            return schema.model_validate(LLMClient._extract_json(raw))
        except Exception:
            fix = f"Your previous answer was not valid JSON. Return ONLY the JSON object.\nPrevious answer:\n{raw}"
            raw2 = await self.complete(json_system, fix)
            try:
                return schema.model_validate(LLMClient._extract_json(raw2))
            except Exception as exc:
                raise LLMError(f"LLM returned invalid JSON for {schema.__name__}: {exc}") from exc
