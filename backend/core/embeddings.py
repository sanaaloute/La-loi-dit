"""Embedding providers.

Production uses the configured OpenAI-compatible embedding model via
LiteLLM. When no API key is configured, a deterministic hashing embedder
keeps the whole stack (vector search, memory, tests) fully functional
offline — same interface, no external calls.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import litellm

from backend.core.config import Settings


class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LiteLLMEmbeddings:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dimension = settings.embedding_dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        kwargs = {"model": self.settings.embedding_model, "input": texts}
        # Prefer dedicated embedding endpoint credentials, fall back to LLM settings
        # for the common single-provider case.
        if self.settings.embedding_api_key:
            kwargs["api_key"] = self.settings.embedding_api_key
        elif self.settings.llm_api_key:
            kwargs["api_key"] = self.settings.llm_api_key
        if self.settings.embedding_api_base:
            kwargs["api_base"] = self.settings.embedding_api_base
        elif self.settings.llm_api_base:
            kwargs["api_base"] = self.settings.llm_api_base
        # Ollama Cloud requires the API key as a Bearer token; LiteLLM's
        # ollama provider does not add it automatically (same special case
        # as the chat client in core/llm.py).
        if self.settings.embedding_model.startswith("ollama/") and kwargs.get("api_key"):
            kwargs["extra_headers"] = {"Authorization": f"Bearer {kwargs['api_key']}"}
        resp = await litellm.aembedding(**kwargs)
        return [list(item["embedding"]) for item in resp.data]


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
    # configured or the LLM provider has credentials (single-provider setups).
    if (
        settings.embedding_api_base
        or settings.embedding_api_key
        or (settings.llm_api_key and settings.llm_provider != "mock")
    ):
        return LiteLLMEmbeddings(settings)
    return HashEmbeddings(settings.embedding_dimension)
