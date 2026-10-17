"""Local Ollama and deterministic mock chat clients built on LiteLLM.

Production chat uses only keyless local Ollama models. The mock provider stays
available for offline tests and development. Embeddings and transcription use
separate adapters; their configuration is intentionally outside this module.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

import litellm
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.exceptions import LLMError
from backend.observability.langfuse_client import litellm_trace_metadata

T = TypeVar("T", bound=BaseModel)

litellm.drop_params = True

PROVIDER_PREFIXES = {
    "ollama": "ollama/",
    "mock": "mock/",
}

PROVIDER_DEFAULT_API_BASES = {
    "ollama": "http://localhost:11434",
}

CLASSIFICATION_MODEL = "nemotron-3-nano:4b"
SYNTHESIS_MODEL = "gemma4:12b"


def default_ollama_api_base() -> str:
    """Use the Mac host's Ollama from Docker, localhost otherwise."""
    if Path("/.dockerenv").exists():
        return "http://host.docker.internal:11434"
    return PROVIDER_DEFAULT_API_BASES["ollama"]


class LLMClient:
    """Thin async wrapper with JSON-mode parsing and one corrective retry."""

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
        if self.provider not in PROVIDER_PREFIXES:
            raise ValueError(f"unsupported chat provider: {self.provider}")
        default_model = settings.llm_model if self.provider == "mock" else SYNTHESIS_MODEL
        raw_model = model if model is not None else default_model
        namespace = f"{self.provider}/"
        if raw_model.startswith(namespace):
            raw_model = raw_model[len(namespace):]
        self.model = f"{PROVIDER_PREFIXES[self.provider]}{raw_model}"
        self.api_key = settings.llm_api_key if api_key is None else api_key
        self.api_base = api_base if api_base is not None else settings.llm_api_base
        if not self.api_base and self.provider == "ollama":
            self.api_base = default_ollama_api_base()
        self.timeout_seconds = settings.llm_timeout_seconds
        # Cumulative token metering across calls on this instance. Read as a
        # delta around one request when the client is shared (mock mode).
        self.usage_totals: dict[str, int] = {"tokens_in": 0, "tokens_out": 0}

    # ------------------------------------------------------------------
    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Offline heuristic (~4 chars/token) used for mock/usage-less calls."""
        return max(1, len(text) // 4) if text else 0

    @staticmethod
    def _clean_channel_content(content: str) -> str:
        lowered = content.lower()
        final_marker = "<|channel>final"
        final_index = lowered.find(final_marker)
        if final_index >= 0:
            return content[final_index + len(final_marker):].strip()
        if lowered.lstrip().startswith("<|channel>thought"):
            return ""
        return content

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

    async def _ollama_complete(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout_seconds: float,
    ) -> str:
        from ollama import AsyncClient

        host = self.api_base or PROVIDER_DEFAULT_API_BASES["ollama"]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        client = AsyncClient(host=host, headers=headers)
        model = self.model
        if model.startswith("ollama/"):
            model = model[len("ollama/"):]
        try:
            response = await asyncio.wait_for(
                client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    stream=False,
                    keep_alive=self.settings.ollama_keep_alive,
                    think=self.settings.ollama_think_enabled,
                    options={
                        "temperature": self.settings.llm_temperature if temperature is None else temperature,
                        "num_predict": max_tokens or self.settings.llm_max_tokens,
                    },
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(f"Ollama call timed out after {timeout_seconds}s ({self.model})") from exc
        except Exception as exc:
            raise LLMError(f"Ollama call failed ({self.model}): {exc}") from exc
        content = self._clean_channel_content(response.message.content or "")
        usage = {
            "prompt_tokens": getattr(response, "prompt_eval_count", None),
            "completion_tokens": getattr(response, "eval_count", None),
        }
        self._record_usage(system, user, content, usage)
        return content

    # ------------------------------------------------------------------
    async def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        if self.provider == "mock":
            result = self._mock_complete(system, user)
            self._record_usage(system, user, result, None)
            return result
        request_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if self.provider == "ollama":
            return await self._ollama_complete(
                system,
                user,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=request_timeout,
            )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "timeout": request_timeout,
            "num_retries": 0,
            "metadata": litellm_trace_metadata(),
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        extra_headers: dict[str, str] = {}
        if self.provider == "ollama":
            kwargs["keep_alive"] = self.settings.ollama_keep_alive
            kwargs["think"] = self.settings.ollama_think_enabled
            if self.api_key:
                extra_headers["Authorization"] = f"Bearer {self.api_key}"
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        try:
            resp = await asyncio.wait_for(
                litellm.acompletion(**kwargs), timeout=request_timeout
            )
            content = self._clean_channel_content(resp.choices[0].message.content or "")
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

        Local Ollama native tool calling is attempted first; manual JSON mode
        remains the deterministic fallback for models without native support.
        """
        if self.provider == "mock":
            return self._mock_complete_tools(messages, tools)

        if tools:
            try:
                return await self._ollama_tools(messages, tools, temperature, max_tokens)
            except Exception:
                pass

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

        host = self.api_base or PROVIDER_DEFAULT_API_BASES["ollama"]
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
                    keep_alive=self.settings.ollama_keep_alive,
                    think=self.settings.ollama_think_enabled,
                    options={
                        "temperature": self.settings.llm_temperature if temperature is None else temperature,
                        "num_predict": max_tokens or self.settings.llm_max_tokens,
                    },
                ),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(
                f"Ollama tool call timed out after {self.timeout_seconds}s ({self.model})"
            ) from exc
        except Exception as exc:
            raise LLMError(f"Ollama tool call failed ({self.model}): {exc}") from exc
        message = response.message
        if getattr(message, "tool_calls", None):
            calls = []
            for tc in message.tool_calls:
                calls.append({"name": tc.function.name, "arguments": tc.function.arguments})
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
        if "traducteur" in s:
            # Language gate (QUERY_TRANSLATE_SYSTEM): no real translator
            # offline, so translation is a no-op echo — the pipeline keeps
            # running deterministically on the original query.
            return json.dumps({"language": "fr", "french": user[len("Texte: "):] if user.startswith("Texte: ") else user})
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
    if settings.llm_provider.lower() == "mock":
        return LLMClient(settings)
    return LLMClient(
        settings,
        provider="ollama",
        model=SYNTHESIS_MODEL,
        api_key="",
        api_base=settings.llm_api_base,
    )
