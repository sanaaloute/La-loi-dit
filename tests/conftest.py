"""Shared pytest fixtures — fully offline (mock LLM, in-memory adapters).

Imports of subsystems built in parallel (vectorstore, retrieval, memory)
stay inside the fixtures so collection never breaks while they are mid-build.
"""

from __future__ import annotations

import pytest

import backend.observability.langfuse_client as lf_client
from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.context import AppContext
from backend.core.embeddings import HashEmbeddings
from backend.core.llm import LLMClient


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Offline settings: mock LLM, SQLite in tmp dir, tmp data dir."""
    return Settings(
        llm_provider="mock",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        data_dir=tmp_path,
    )


@pytest.fixture
async def ctx(settings: Settings) -> AppContext:
    """AppContext with InMemoryVectorStore + RetrievalCoordinator + MemoryStore."""
    from backend.memory.store import MemoryStore
    from backend.retrieval.coordinator import RetrievalCoordinator
    from backend.vectorstore.memory_store import InMemoryVectorStore

    cache = InMemoryCache(settings.cache_ttl_seconds)
    embedder = HashEmbeddings(settings.embedding_dimension)
    context = AppContext(
        settings=settings,
        llm=LLMClient(settings),
        cache=cache,
        embedder=embedder,
    )
    context.vector_store = InMemoryVectorStore()
    context.retriever = RetrievalCoordinator(context)
    context.memory = MemoryStore(settings, cache, embedder)
    return context


@pytest.fixture
async def seeded_ctx(ctx: AppContext) -> AppContext:
    """ctx with the synthetic seed evidence embedded and upserted."""
    from backend.evaluation.seed_data import seed_evidence

    chunks = seed_evidence()
    vectors = await ctx.embedder.embed([c.content for c in chunks])
    await ctx.vector_store.upsert(chunks, vectors)
    return ctx


@pytest.fixture
async def graph(ctx: AppContext):
    """Compiled LangGraph workflow over the empty-store ctx."""
    from backend.workflows.graph import build_graph

    return build_graph(ctx)


@pytest.fixture
async def seeded_graph(seeded_ctx: AppContext):
    """Compiled LangGraph workflow over the seeded ctx."""
    from backend.workflows.graph import build_graph

    return build_graph(seeded_ctx)


@pytest.fixture(autouse=True)
def disable_langfuse_network_calls(monkeypatch):
    """Keep Langfuse observability code paths active but avoid network I/O.

    Tests verify that tracing integrates cleanly (no crashes, trace_id field
    present) without requiring a running Langfuse server.
    """
    monkeypatch.setattr(lf_client, "get_langfuse", lambda _settings=None: None)
    yield
