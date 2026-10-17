import asyncio

from backend.core.models import AuthorityLevel, EvidenceChunk
from backend.retrieval.bm25 import BM25Retriever
from backend.retrieval.fusion import reciprocal_rank_fusion
from backend.retrieval.scoring import bm25_to_unit, source_reliability
from backend.vectorstore.memory_store import InMemoryVectorStore


def test_rrf_preserves_absolute_retriever_scores():
    weak = EvidenceChunk(chunk_id="weak", retrieval_score=0.2)
    strong = EvidenceChunk(chunk_id="strong", retrieval_score=0.8)

    fused = reciprocal_rank_fusion([[weak, strong], [strong]], k=60)

    assert fused[0].chunk_id == "strong"
    assert fused[0].fusion_score == 1.0
    assert fused[0].retrieval_score == 0.8
    assert fused[1].retrieval_score == 0.2


def test_bm25_and_vector_scores_are_absolute_not_forced_to_one():
    assert 0.0 < bm25_to_unit(1.0) < 1.0
    chunks = [
        EvidenceChunk(chunk_id="match", content="licenciement préavis"),
        EvidenceChunk(chunk_id="other", content="divorce"),
        EvidenceChunk(chunk_id="third", content="constitution laïque"),
    ]
    retriever = BM25Retriever()
    retriever.add_documents(chunks)

    result = retriever.search("licenciement", top_k=1)

    assert result[0].chunk_id == "match"
    assert 0.0 < result[0].retrieval_score < 1.0

    store = InMemoryVectorStore()
    asyncio.run(store.upsert(chunks, [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]))
    vector_result = asyncio.run(store.search([1.0, 0.0], top_k=3))
    assert vector_result[0].retrieval_score == 1.0
    assert vector_result[1].retrieval_score == 0.0


def test_source_reliability_defaults_only_when_missing():
    unset = EvidenceChunk(authority=AuthorityLevel.LAW, confidence=0.0)
    independent = EvidenceChunk(authority=AuthorityLevel.LAW, confidence=0.8)

    assert source_reliability(unset) == 0.5
    assert source_reliability(independent) == 0.8
