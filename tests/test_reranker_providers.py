"""Reranker provider tests: factory, API cross-encoder, fallback, wiring."""

from __future__ import annotations

import json

import httpx

from backend.core.config import Settings
from backend.core.embeddings import HashEmbeddings
from backend.core.models import EvidenceChunk, SearchKind, SearchTask
from backend.retrieval.reranker_providers import (
    ApiCrossEncoderReranker,
    HeuristicReranker,
    get_reranker,
)


def _chunk(content: str) -> EvidenceChunk:
    return EvidenceChunk(document_name="Doc", content=content)


def _rerank_handler(scores: list[float] | None = None, status: int = 200, calls: list | None = None):
    """MockTransport handler speaking the Cohere-style /rerank response shape."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        documents = json.loads(request.content)["documents"]
        results = [
            {
                "index": i,
                "relevance_score": scores[i] if scores else float(len(documents) - i),
            }
            for i in range(len(documents))
        ]
        return httpx.Response(200, json={"results": results})

    return handler


def test_factory_defaults_to_heuristic(settings: Settings):
    reranker = get_reranker(settings, embedder=HashEmbeddings())
    assert isinstance(reranker, HeuristicReranker)


def test_factory_api_without_credentials_falls_back(settings: Settings, caplog):
    settings.reranker_provider = "api"
    # No reranker_api_base/key/model configured.
    with caplog.at_level("WARNING"):
        reranker = get_reranker(settings, embedder=HashEmbeddings())
    assert isinstance(reranker, HeuristicReranker)
    assert any("reranker" in record.message for record in caplog.records)


def test_factory_api_with_credentials_returns_api_reranker(settings: Settings):
    settings.reranker_provider = "api"
    settings.reranker_api_base = "https://rerank.example.test/v1"
    settings.reranker_api_key = "sk-test"
    settings.reranker_model = "bge-reranker-v2-m3"
    reranker = get_reranker(settings, embedder=HashEmbeddings())
    assert isinstance(reranker, ApiCrossEncoderReranker)


async def test_api_reranker_applies_scores_and_sorts(settings: Settings):
    settings.reranker_api_base = "https://rerank.example.test/v1"
    settings.reranker_api_key = "sk-test"
    settings.reranker_model = "rerank-multilingual-v3.0"
    calls: list = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_rerank_handler(scores=[0.1, 0.9, 0.5], calls=calls))
    )
    reranker = ApiCrossEncoderReranker(settings, client=client)
    chunks = [_chunk("alpha"), _chunk("beta"), _chunk("gamma")]
    ranked = await reranker.rerank("requête", chunks)
    assert [c.rerank_score for c in ranked] == [0.9, 0.5, 0.1]
    assert ranked[0].content == "beta"
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["model"] == "rerank-multilingual-v3.0"
    assert body["documents"] == ["alpha", "beta", "gamma"]
    assert calls[0].headers["Authorization"] == "Bearer sk-test"


async def test_api_reranker_falls_back_to_heuristic_on_failure(settings: Settings, caplog):
    settings.reranker_api_base = "https://rerank.example.test/v1"
    settings.reranker_api_key = "sk-test"
    settings.reranker_model = "bge-reranker-v2-m3"
    calls: list = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_rerank_handler(status=500, calls=calls))
    )
    reranker = ApiCrossEncoderReranker(settings, client=client)
    chunks = [_chunk("préavis licenciement indemnité"), _chunk("hors sujet complet")]
    with caplog.at_level("WARNING"):
        ranked = await reranker.rerank("préavis licenciement", chunks)  # must not raise
    assert len(calls) == 2  # initial attempt + one retry
    assert any("falling back to heuristic" in r.message for r in caplog.records)
    # Heuristic fallback scored and sorted every chunk (off-topic chunk may
    # legitimately score 0.0 with the offline heuristic).
    assert len(ranked) == 2
    assert ranked[0].rerank_score >= ranked[1].rerank_score
    assert ranked[0].content == "préavis licenciement indemnité"
    assert ranked[0].rerank_score > 0.0


async def test_api_reranker_batches_large_inputs(settings: Settings):
    settings.reranker_api_base = "https://rerank.example.test/v1"
    settings.reranker_api_key = "sk-test"
    settings.reranker_model = "qwen-reranker"
    settings.reranker_batch_size = 2
    calls: list = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_rerank_handler(calls=calls))
    )
    reranker = ApiCrossEncoderReranker(settings, client=client)
    chunks = [_chunk(f"doc {i}") for i in range(5)]
    ranked = await reranker.rerank("requête", chunks)
    assert len(calls) == 3  # batches of 2, 2, 1
    batch_sizes = [len(json.loads(c.content)["documents"]) for c in calls]
    assert batch_sizes == [2, 2, 1]
    # Scores line up with the right chunks across batch boundaries.
    assert len(ranked) == 5
    assert all(c.rerank_score > 0.0 for c in ranked)
    assert [c.rerank_score for c in ranked] == sorted(
        [c.rerank_score for c in ranked], reverse=True
    )


async def test_coordinator_uses_injected_reranker(seeded_ctx):
    import backend.retrieval.coordinator as coord_module

    class _FakeReranker:
        def __init__(self):
            self.calls: list = []

        async def rerank(self, query, chunks):
            self.calls.append((query, list(chunks)))
            for chunk in chunks:
                # Satisfy the coordinator's relevance floor.
                chunk.metadata["shared_tokens"] = 99
                chunk.metadata["query_similarity"] = 0.9
                chunk.rerank_score = 0.5
            return list(reversed(chunks))

    fake = _FakeReranker()
    coordinator = coord_module.RetrievalCoordinator(seeded_ctx, reranker=fake)
    tasks = [SearchTask(kind=SearchKind.VECTOR, query="préavis licenciement", top_k=4)]
    results = await coordinator.retrieve(tasks)
    assert fake.calls, "coordinator must route reranking through the injected provider"
    assert results
    expected_ids = [c.chunk_id for c in reversed(fake.calls[0][1])]
    assert [c.chunk_id for c in results] == expected_ids[: len(results)]
