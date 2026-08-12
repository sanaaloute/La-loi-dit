"""Shared pytest fixtures — fully offline (mock LLM, in-memory adapters).

Imports of subsystems built in parallel (vectorstore, retrieval, memory)
stay inside the fixtures so collection never breaks while they are mid-build.
"""

from __future__ import annotations

import os

import pytest

import backend.observability.langfuse_client as lf_client
from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.context import AppContext
from backend.core.embeddings import HashEmbeddings
from backend.core.llm import LLMClient


@pytest.fixture(autouse=True, scope="session")
def isolate_from_local_env():
    """Hermetic tests: the developer's `.env` / LEGAL_AI_* shell vars must
    never leak into the suite (a production-like .env turns the embedder into
    real network calls and flips strict-mode behavior). Individual tests can
    still set LEGAL_AI_* explicitly via monkeypatch."""
    from backend.core.config import get_settings

    stripped = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("LEGAL_AI_")}
    original_env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    # pymilvus calls dotenv.load_dotenv() at import time (lazily triggered by
    # MilvusVectorStore.connect mid-session), which would re-pollute os.environ
    # with the developer's .env AFTER the strip above. Neutralize it for the
    # session so later Settings() instances stay hermetic.
    import dotenv
    import dotenv.main

    real_load_dotenv = dotenv.main.load_dotenv

    def _no_dotenv(*args, **kwargs):  # pragma: no cover - test guard
        return False

    dotenv.load_dotenv = _no_dotenv
    dotenv.main.load_dotenv = _no_dotenv
    get_settings.cache_clear()
    yield
    dotenv.load_dotenv = real_load_dotenv
    dotenv.main.load_dotenv = real_load_dotenv
    Settings.model_config["env_file"] = original_env_file
    os.environ.update(stripped)
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Offline settings: mock LLM, SQLite in tmp dir, tmp data dir."""
    return Settings(
        llm_provider="mock",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        data_dir=tmp_path,
        rate_limit_per_minute=1_000_000,
        rate_limit_per_second=1_000_000,
        single_session_per_user=False,
        guardrails_enabled=False,
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
