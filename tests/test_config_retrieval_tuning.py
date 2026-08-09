"""Config-promoted retrieval tuning knobs: defaults preserve the old
hardcoded literals, and overrides actually change behavior."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from backend.core.config import Settings
from backend.core.models import (
    AuthorityLevel,
    EvidenceChunk,
    SearchKind,
    SearchTask,
)

TODAY = date(2026, 8, 9)


def _chunk(content: str = "préavis licenciement indemnité", **kwargs) -> EvidenceChunk:
    kwargs.setdefault("document_name", "Code du travail")
    return EvidenceChunk(content=content, **kwargs)


# --------------------------------------------------------------------- defaults


def test_defaults_match_previous_hardcoded_values():
    s = Settings()
    assert s.retrieval_dense_similarity_floor_cap == 0.45
    assert s.retrieval_discriminative_df_ratio == 0.2
    assert s.rerank_llm_excerpt_chars == 300
    assert s.rerank_llm_blend_weight == 0.5
    assert s.reranker_max_retries == 1
    assert s.graph_expansion_score == 0.01
    assert s.graph_expansion_sources == 3
    assert s.graph_expansion_limit == 8
    assert s.temporal_score_unknown == 0.3
    assert s.temporal_score_repealed_before_date == 0.1
    assert s.temporal_score_unconfirmed == 0.5
    assert s.search_web_hit_score == 0.5
    assert s.search_authority_fallback == 0.15
    assert s.milvus_connect_attempts == 3
    assert s.milvus_connect_backoff_seconds == 1.0
    assert s.embedding_batch_size == 200


# ---------------------------------------------------------------------- temporal


def test_temporal_scores_overridable():
    from backend.retrieval.temporal import temporal_score

    custom = Settings(
        temporal_score_unknown=0.9,
        temporal_score_unconfirmed=0.6,
        temporal_score_repealed_before_date=0.2,
    )
    unknown = _chunk(status="unknown")
    assert temporal_score(unknown, "current", today=TODAY, settings=custom) == 0.9
    assert (
        temporal_score(
            unknown, "historical", scenario_date=date(2015, 1, 1), today=TODAY, settings=custom
        )
        == 0.6
    )
    repealed = _chunk(valid_from=date(1990, 1, 1), valid_until=date(2000, 1, 1))
    assert (
        temporal_score(
            repealed, "historical", scenario_date=date(2015, 1, 1), today=TODAY, settings=custom
        )
        == 0.2
    )
    # Defaults still reproduce the previous literals.
    default = Settings()
    assert temporal_score(unknown, "current", today=TODAY, settings=default) == 0.3


# ---------------------------------------------------------------------- reranker


class _FixedScoreLLM:
    """Fake LLM returning a constant rescore; records the user prompt."""

    def __init__(self, score: float = 0.9):
        self.score = score
        self.prompts: list[str] = []

    async def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return f"[{self.score}]"


async def test_rerank_llm_blend_weight_changes_score(monkeypatch):
    import backend.retrieval.reranker as reranker_module

    async def _score_with(blend: float) -> float:
        monkeypatch.setattr(
            reranker_module, "get_settings", lambda: Settings(rerank_llm_blend_weight=blend)
        )
        ranked = await reranker_module.rerank(
            "préavis licenciement", [_chunk()], top_k=1, llm=_FixedScoreLLM(0.9)
        )
        return ranked[0].rerank_score

    # blend 1.0: the LLM rescore fully determines the final score.
    assert await _score_with(1.0) == pytest.approx(0.9)
    # blend 0.0: heuristic only (max 0.75 * cosine < 0.9 with default weights).
    assert await _score_with(0.0) != pytest.approx(0.9)


async def test_rerank_llm_excerpt_chars_truncates_prompt(monkeypatch):
    import backend.retrieval.reranker as reranker_module

    monkeypatch.setattr(
        reranker_module, "get_settings", lambda: Settings(rerank_llm_excerpt_chars=10)
    )
    llm = _FixedScoreLLM(0.5)
    await reranker_module.rerank("query", [_chunk(content="x" * 100)], top_k=1, llm=llm)
    assert llm.prompts, "LLM rescore should have been called"
    assert "[0] " + "x" * 10 in llm.prompts[0]
    assert "x" * 11 not in llm.prompts[0]


# ----------------------------------------------------------- api reranker retries


def _failing_rerank_client(calls: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"error": "boom"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_api_reranker_retry_budget_overridable(settings):
    from backend.retrieval.reranker_providers import ApiCrossEncoderReranker

    settings.reranker_api_base = "https://rerank.example.test/v1"
    settings.reranker_api_key = "sk-test"
    settings.reranker_model = "bge-reranker-v2-m3"

    calls: list = []
    settings.reranker_max_retries = 2
    reranker = ApiCrossEncoderReranker(settings, client=_failing_rerank_client(calls))
    await reranker.rerank("requête", [_chunk()])  # falls back, never raises
    assert len(calls) == 3  # initial attempt + 2 retries

    calls = []
    settings.reranker_max_retries = 0
    reranker = ApiCrossEncoderReranker(settings, client=_failing_rerank_client(calls))
    await reranker.rerank("requête", [_chunk()])
    assert len(calls) == 1  # no retry budget


# --------------------------------------------------------------- graph expansion


async def _seed_graph(ctx, articles: list[str], edges: list[tuple[str, str]]):
    from backend.knowledge.models import (
        ExtractedRelationship,
        LegalArticleRecord,
        LegalDocumentRecord,
        RelationType,
    )
    from backend.knowledge.store import graph_store_for

    store = graph_store_for(ctx)
    await store.upsert_document(
        LegalDocumentRecord(document_id="d", name="Code du travail", law_number="L1")
    )
    await store.upsert_articles(
        "d", [LegalArticleRecord(document_id="d", article=a) for a in articles]
    )
    await store.add_relationships(
        [
            ExtractedRelationship(
                src_document="d",
                src_article=src,
                relation=RelationType.REFERENCES,
                dst_document="d",
                dst_article=dst,
            )
            for src, dst in edges
        ]
    )
    chunks = [
        EvidenceChunk(
            document_id="d",
            document_name="Code du travail",
            article=a,
            content=f"Contenu de l'article {a} du code du travail.",
        )
        for a in articles
    ]
    vectors = await ctx.embedder.embed([c.content for c in chunks])
    await ctx.vector_store.upsert(chunks, vectors)
    return chunks


async def test_graph_expansion_knobs_overridable(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    chunks = await _seed_graph(ctx, ["1", "2", "3"], [("1", "2"), ("1", "3")])
    worker = GraphWorker(ctx)

    expanded = await worker.expand([chunks[0]])
    assert len(expanded) == 3  # both referenced articles appended (default limit 8)
    assert all(c.retrieval_score == 0.01 for c in expanded[1:])  # default score

    ctx.settings.graph_expansion_limit = 1
    ctx.settings.graph_expansion_score = 0.42
    expanded = await worker.expand([chunks[0]])
    assert len(expanded) == 2  # cap respected: only one candidate appended
    assert expanded[1].retrieval_score == 0.42


# --------------------------------------------------------------- milvus factory


async def test_milvus_connect_attempts_and_backoff_overridable(monkeypatch):
    milvus_store = pytest.importorskip("backend.vectorstore.milvus_store")
    from backend.vectorstore.factory import get_vector_store
    from backend.vectorstore.memory_store import InMemoryVectorStore

    attempts: list = []

    class _FailingStore:
        def __init__(self, settings):
            attempts.append(1)

        async def connect(self):
            raise ConnectionError("boom")

    monkeypatch.setattr(milvus_store, "MilvusVectorStore", _FailingStore)
    settings = Settings(
        milvus_enabled=True,
        milvus_connect_attempts=2,
        milvus_connect_backoff_seconds=0.0,  # keep the test instant
    )
    store = await get_vector_store(settings)
    assert isinstance(store, InMemoryVectorStore)
    assert len(attempts) == 2

    attempts.clear()
    settings.milvus_connect_attempts = 1
    store = await get_vector_store(settings)
    assert isinstance(store, InMemoryVectorStore)
    assert len(attempts) == 1


# ------------------------------------------------------------- embedding batches


async def test_embedding_batch_size_overridable(monkeypatch):
    import backend.core.embeddings as embeddings_module
    from backend.core.embeddings import LiteLLMEmbeddings

    batch_sizes: list[int] = []

    class _Resp:
        def __init__(self, n: int):
            self.data = [{"embedding": [0.1, 0.2]} for _ in range(n)]

    async def fake_aembedding(**kwargs):
        batch_sizes.append(len(kwargs["input"]))
        return _Resp(len(kwargs["input"]))

    monkeypatch.setattr(embeddings_module.litellm, "aembedding", fake_aembedding)
    embedder = LiteLLMEmbeddings(Settings(embedding_batch_size=2))
    vectors = await embedder.embed(["a", "b", "c", "d", "e"])
    assert batch_sizes == [2, 2, 1]
    assert len(vectors) == 5


# ------------------------------------------------------------- web orchestrator


def test_orchestrator_chunk_scores_overridable(monkeypatch):
    import backend.search.orchestrator as orchestrator_module
    from backend.search.sources import OfficialSource

    source = OfficialSource(
        name="Journal officiel",
        base_url="https://jo.gouv.bf",
        authority=AuthorityLevel.OFFICIAL_GAZETTE,
        kind=SearchKind.REGULATION,
        government_body="Gouvernement",
    )
    task = SearchTask(kind=SearchKind.REGULATION, query="loi")
    raw = {"title": "Loi n° 1", "url": "https://jo.gouv.bf/loi-1", "content": "texte"}

    default_chunk = orchestrator_module._to_chunk(raw, source, task, 1200, Settings())
    assert default_chunk.retrieval_score == 0.5

    custom = Settings(search_web_hit_score=0.77, search_authority_fallback=0.33)
    chunk = orchestrator_module._to_chunk(raw, source, task, 1200, custom)
    assert chunk.retrieval_score == 0.77

    # Authority fallback fires only for authorities missing from the table.
    monkeypatch.setattr(orchestrator_module, "AUTHORITY_WEIGHTS", {})
    chunk = orchestrator_module._to_chunk(raw, source, task, 1200, custom)
    assert chunk.confidence == 0.33
    chunk = orchestrator_module._to_chunk(raw, source, task, 1200, Settings())
    assert chunk.confidence == 0.15
