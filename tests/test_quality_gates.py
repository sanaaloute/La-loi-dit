import asyncio
from datetime import date
from types import SimpleNamespace

from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.embeddings import HashEmbeddings
from backend.core.models import EvidenceChunk, SearchKind, SearchTask
from backend.retrieval.coordinator import RetrievalCoordinator


def test_coverage_ratio_rounds_up(tmp_path):
    from backend.agents.coverage_auditor import question_is_covered

    settings = Settings(llm_provider="mock", data_dir=tmp_path, coverage_term_match_ratio=0.5)

    assert not question_is_covered("alpha bravo charlie", ["alpha"], set(), settings)
    assert question_is_covered("alpha bravo charlie", ["alpha bravo"], set(), settings)


def test_temporal_filter_does_not_restore_inapplicable_evidence(tmp_path, monkeypatch):
    settings = Settings(llm_provider="mock", data_dir=tmp_path, embedding_dimension=8)
    repealed = EvidenceChunk(
        chunk_id="old-law",
        content="ancienne règle",
        status="repealed",
        valid_until=date(2020, 1, 1),
    )

    class Worker:
        async def run(self, task):
            return [repealed]

    class Reranker:
        async def rerank(self, query, chunks):
            return chunks

    monkeypatch.setattr("backend.retrieval.coordinator.worker_for", lambda kind, ctx: Worker())
    ctx = SimpleNamespace(
        settings=settings,
        cache=InMemoryCache(),
        embedder=HashEmbeddings(8),
        extras={},
        llm=None,
    )
    coordinator = RetrievalCoordinator(ctx, reranker=Reranker())

    result = asyncio.run(
        coordinator.retrieve(
            [SearchTask(kind=SearchKind.VECTOR, query="règle actuelle")],
            temporal_intent="current",
        )
    )

    assert result == []
