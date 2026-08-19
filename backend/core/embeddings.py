"""Embedding providers.

Production uses the configured OpenAI-compatible embedding model via
LiteLLM. When no API key is configured, a deterministic hashing embedder
keeps the whole stack (vector search, memory, tests) fully functional
offline — same interface, no external calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from typing import Optional, Protocol

import litellm
from backend.core.config import Settings

# LiteLLM is chatty when an embedding model is not in its internal cost map;
# these calls succeed against OpenAI-compatible endpoints, so keep the logs useful
# without dumping provider-list banners to stdout.
litellm.set_verbose = False
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)



class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LiteLLMEmbeddings:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dimension = settings.embedding_dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Batched API calls: providers cap batch sizes (NVIDIA via OpenRouter
        # caps at 256); the default of 200 stays below that.
        batch_size = max(1, self.settings.embedding_batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(await self._embed_batch(texts[start : start + batch_size]))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = self.settings.embedding_model
        embed_model = model
        if embed_model.startswith("openrouter/"):
            # LiteLLM's embedding() has no openrouter branch in this version
            # ("No valid embedding model args passed in"); OpenRouter exposes an
            # OpenAI-compatible /embeddings, so route through the openai provider.
            embed_model = "openai/" + embed_model[len("openrouter/"):]
        kwargs = {"model": embed_model, "input": texts}
        if model.startswith("openrouter/"):
            # NVIDIA embedding models reject LiteLLM's default base64 encoding.
            kwargs["encoding_format"] = "float"
        # Dedicated embedding credentials first, then provider-specific keys,
        # then the main LLM key (single-provider setups).
        if self.settings.embedding_api_key:
            kwargs["api_key"] = self.settings.embedding_api_key
        elif model.startswith("openrouter/") and self.settings.openrouter_api_key:
            kwargs["api_key"] = self.settings.openrouter_api_key
        elif self.settings.llm_api_key:
            kwargs["api_key"] = self.settings.llm_api_key
        if self.settings.embedding_api_base:
            kwargs["api_base"] = self.settings.embedding_api_base
        elif model.startswith("openrouter/"):
            kwargs["api_base"] = "https://openrouter.ai/api/v1"
        elif self.settings.llm_api_base:
            kwargs["api_base"] = self.settings.llm_api_base
        # Auth/attribution headers LiteLLM does not add on its own:
        # - Ollama Cloud requires the API key as a Bearer token (same special
        #   case as the chat client in core/llm.py).
        # - OpenRouter accepts optional rankings headers.
        extra_headers: dict[str, str] = {}
        if model.startswith("ollama/") and kwargs.get("api_key"):
            extra_headers["Authorization"] = f"Bearer {kwargs['api_key']}"
        if model.startswith("openrouter/"):
            # HTTP headers must be ASCII; the configured app name may not be.
            safe_app_name = self.settings.app_name.encode("ascii", "ignore").decode("ascii")
            extra_headers.setdefault("HTTP-Referer", safe_app_name)
            extra_headers.setdefault("X-Title", safe_app_name)
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        # Hard per-call timeout: without it a wedged embedding server (or a
        # dead keep-alive connection) hangs the coroutine forever and freezes
        # the whole ingestion run; a bounded failure marks the document
        # failed — re-ingestable — and the run continues with the next one.
        kwargs["timeout"] = self.settings.llm_timeout_seconds
        # Bounded retries with backoff: the embedding server (Ollama under
        # memory pressure in particular) occasionally drops a connection
        # mid-request; one transient hiccup must not fail a whole document.
        transient = (
            litellm.APIConnectionError,
            litellm.Timeout,
            litellm.InternalServerError,
            litellm.RateLimitError,
            litellm.ServiceUnavailableError,
        )
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = await litellm.aembedding(**kwargs)
                return [list(item["embedding"]) for item in resp.data]
            except transient as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))
        assert last_exc is not None
        raise last_exc


class HashEmbeddings:
    """Deterministic bag-of-hashed-token-embeddings for offline dev/tests."""

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = text.lower().split()
        for token in tokens:
            h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
            vec[h % self.dimension] += 1.0
            # bigram signal for slightly better locality
            vec[(h >> 32) % self.dimension] += 0.5
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def get_embedder(settings: Settings) -> EmbeddingProvider:
    # Use a real embedding model when either a dedicated embedding endpoint is
    # configured or a provider key exists (single-provider setups, Ollama
    # Cloud, or an OpenRouter-served embedding model).
    if (
        settings.embedding_api_base
        or settings.embedding_api_key
        or (settings.llm_api_key and settings.llm_provider != "mock")
        or (settings.embedding_model.startswith("openrouter/") and settings.openrouter_api_key)
    ):
        return LiteLLMEmbeddings(settings)
    return HashEmbeddings(settings.embedding_dimension)
