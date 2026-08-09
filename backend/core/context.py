"""Application context: wires settings, LLM, cache, embedder, stores and
subsystems together once per process, then hands the bundle to the graph.

Every dependency has an offline fallback so the API and tests boot with no
external services running. In strict infrastructure mode (production by
default) fallbacks still keep the process alive, but each one is recorded in
``ctx.infra_status`` as "degraded: ..." and surfaced by /ready instead of
being silent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.core.cache import CacheProtocol, InMemoryCache, RedisCache, get_cache
from backend.core.config import Settings, get_settings
from backend.core.embeddings import EmbeddingProvider, get_embedder
from backend.core.llm import LLMClient, get_llm
from backend.core.model_router import with_failover
from backend.core.ports import MemoryStoreProtocol, RetrieverProtocol, VectorStoreProtocol
from backend.observability.langfuse_client import register_litellm_callbacks

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    llm: LLMClient
    cache: CacheProtocol
    embedder: EmbeddingProvider
    vector_store: Optional[VectorStoreProtocol] = None
    retriever: Optional[RetrieverProtocol] = None
    memory: Optional[MemoryStoreProtocol] = None
    user_store: Optional[Any] = None  # backend.users.UserStore (dev-user-only when DB down)
    extras: dict[str, Any] = field(default_factory=dict)
    # Per-dependency health: "ok"/"ok (reason)"/"degraded: reason". Populated
    # by build_context; consumed by /ready and operators.
    infra_status: dict[str, str] = field(default_factory=dict)


async def build_context(settings: Optional[Settings] = None) -> AppContext:
    """Build the default production context (adapters auto-fallback offline)."""
    settings = settings or get_settings()
    settings.ensure_data_dir()

    # Link LiteLLM completions to Langfuse when credentials are configured.
    if settings.langfuse_enabled:
        register_litellm_callbacks(settings)

    ctx = AppContext(
        settings=settings,
        llm=with_failover(get_llm(settings), settings),
        cache=await get_cache(settings),
        embedder=get_embedder(settings),
    )

    from backend.memory.store import MemoryStore

    ctx.memory = MemoryStore(settings, ctx.cache, ctx.embedder)

    from backend.users.service import UserStore

    ctx.user_store = UserStore(settings)  # lazy DB bootstrap on first use

    from backend.vectorstore.factory import get_vector_store

    ctx.vector_store = await get_vector_store(settings)

    from backend.retrieval.coordinator import RetrievalCoordinator

    ctx.retriever = RetrievalCoordinator(ctx)

    ctx.infra_status = await _assess_infra(ctx)
    for dep, status in ctx.infra_status.items():
        if status.startswith("degraded") and settings.strict_infra_enabled:
            logger.error("strict infra: %s %s", dep, status)
    return ctx


async def _assess_infra(ctx: AppContext) -> dict[str, str]:
    """Record per-dependency health after wiring (never raises)."""
    settings = ctx.settings
    status: dict[str, str] = {}

    # --- llm: no cheap probe (a ping would cost an API call) ---
    if ctx.llm.provider == "mock":
        status["llm"] = (
            "degraded: mock llm provider in strict mode"
            if settings.strict_infra_enabled
            else "ok (mock provider)"
        )
    else:
        status["llm"] = f"ok (configured: {ctx.llm.model})"

    # --- redis / cache ---
    if not settings.redis_enabled:
        # Not 503-critical, but production should see multi-worker state is off.
        status["redis"] = (
            "degraded: redis disabled (per-process in-memory cache)"
            if settings.strict_infra_enabled
            else "ok (disabled, in-memory cache)"
        )
    elif isinstance(ctx.cache, RedisCache):
        status["redis"] = "ok"
    else:
        status["redis"] = "degraded: redis unreachable (in-memory fallback)"

    # --- milvus / vector store ---
    from backend.vectorstore.memory_store import InMemoryVectorStore

    if not settings.milvus_enabled:
        # Strict mode: a disabled critical dep is as bad as an unreachable one —
        # production must never silently run on the in-memory fallback.
        status["milvus"] = (
            "degraded: milvus disabled (in-memory store)"
            if settings.strict_infra_enabled
            else "ok (disabled, in-memory store)"
        )
    elif isinstance(ctx.vector_store, InMemoryVectorStore):
        status["milvus"] = "degraded: milvus unreachable (in-memory fallback)"
    else:
        status["milvus"] = "ok"

    # --- postgres / primary database (probe the CONFIGURED url only) ---
    if settings.database_url.startswith("sqlite"):
        status["postgres"] = (
            "degraded: sqlite database in strict mode"
            if settings.strict_infra_enabled
            else "ok (sqlite, development)"
        )
    else:
        from backend.users.service import probe_database

        if await probe_database(settings):
            status["postgres"] = "ok"
        else:
            status["postgres"] = "degraded: postgres unreachable"

    return status
