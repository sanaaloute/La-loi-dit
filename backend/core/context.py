"""Application context: wires settings, LLM, cache, embedder, stores and
subsystems together once per process, then hands the bundle to the graph.

Every dependency has an offline fallback so the API and tests boot with no
external services running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.core.cache import CacheProtocol, get_cache
from backend.core.config import Settings, get_settings
from backend.core.embeddings import EmbeddingProvider, get_embedder
from backend.core.llm import LLMClient, get_llm
from backend.core.ports import MemoryStoreProtocol, RetrieverProtocol, VectorStoreProtocol
from backend.observability.langfuse_client import register_litellm_callbacks


@dataclass
class AppContext:
    settings: Settings
    llm: LLMClient
    cache: CacheProtocol
    embedder: EmbeddingProvider
    vector_store: Optional[VectorStoreProtocol] = None
    retriever: Optional[RetrieverProtocol] = None
    memory: Optional[MemoryStoreProtocol] = None
    extras: dict[str, Any] = field(default_factory=dict)


async def build_context(settings: Optional[Settings] = None) -> AppContext:
    """Build the default production context (adapters auto-fallback offline)."""
    settings = settings or get_settings()
    settings.ensure_data_dir()

    # Link LiteLLM completions to Langfuse when credentials are configured.
    if settings.langfuse_enabled:
        register_litellm_callbacks(settings)

    ctx = AppContext(
        settings=settings,
        llm=get_llm(settings),
        cache=await get_cache(settings),
        embedder=get_embedder(settings),
    )

    from backend.memory.store import MemoryStore

    ctx.memory = MemoryStore(settings, ctx.cache, ctx.embedder)

    from backend.vectorstore.factory import get_vector_store

    ctx.vector_store = await get_vector_store(settings)

    from backend.retrieval.coordinator import RetrievalCoordinator

    ctx.retriever = RetrievalCoordinator(ctx)
    return ctx
