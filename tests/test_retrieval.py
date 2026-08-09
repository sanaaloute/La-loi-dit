"""Retrieval subsystem tests: fusion, dedup, coordinator, ranking (offline)."""

from __future__ import annotations

import inspect

from backend.agents.evidence_ranking import final_score
from backend.core.models import AuthorityLevel, EvidenceChunk, SearchKind, SearchTask


async def _maybe_await(value):
    """Support both sync and async implementations of parallel-built helpers."""
    if inspect.isawaitable(value):
        return await value
    return value


def _chunk(content: str, document_name: str = "Doc") -> EvidenceChunk:
    return EvidenceChunk(document_name=document_name, content=content)


async def test_rrf_fusion_merges_dedupes_and_ranks():
    from backend.retrieval.fusion import reciprocal_rank_fusion

    a, b, c, d = (_chunk(f"contenu {i}") for i in "abcd")
    list1 = [a, b, c]
    list2 = [c, d, a]
    fused = await _maybe_await(reciprocal_rank_fusion([list1, list2]))
    ids = [chunk.chunk_id for chunk in fused]
    assert len(ids) == 4  # merged, duplicates removed
    assert len(set(ids)) == 4
    # a and c appear in both lists -> they outrank b and d
    assert set(ids[:2]) == {a.chunk_id, c.chunk_id}


async def test_deduplicate_drops_near_duplicates():
    from backend.retrieval.dedup import deduplicate

    c1 = _chunk("Le préavis de licenciement est d'un mois pour les employés mensualisés.")
    c2 = _chunk("Le préavis de licenciement est d'un mois pour les employés mensualisés.")
    c3 = _chunk("Le taux de la TVA est fixé à dix-huit pour cent.")
    result = await _maybe_await(deduplicate([c1, c2, c3]))
    assert len(result) == 2


async def test_coordinator_returns_evidence_for_seeded_query(seeded_ctx):
    tasks = [
        SearchTask(
            kind=SearchKind.VECTOR,
            query="préavis de licenciement employé mensualisé Code du travail",
            top_k=5,
        )
    ]
    evidence = await seeded_ctx.retriever.retrieve(tasks)
    assert evidence
    assert any("travail" in c.document_name.lower() for c in evidence)


async def test_coordinator_returns_empty_for_gibberish(seeded_ctx):
    tasks = [SearchTask(kind=SearchKind.VECTOR, query="zzqk xwv qqq zzz blorp", top_k=5)]
    evidence = await seeded_ctx.retriever.retrieve(tasks)
    assert evidence == []


def test_ranking_orders_relevant_chunk_first():
    relevant = EvidenceChunk(
        document_name="Code du travail",
        content="Le préavis est d'un mois pour les employés.",
        retrieval_score=0.9,
        rerank_score=0.9,
        authority=AuthorityLevel.LAW,
        confidence=0.9,
    )
    noise = EvidenceChunk(
        document_name="Blog personnel",
        content="Quelques opinions sur le droit.",
        retrieval_score=0.2,
        rerank_score=0.1,
        authority=AuthorityLevel.BLOG,
        confidence=0.2,
    )
    ranked = sorted([noise, relevant], key=final_score, reverse=True)
    assert ranked[0] is relevant
    assert final_score(relevant) > final_score(noise)


async def test_retrieval_branch_searches_its_subquestion(seeded_ctx):
    """One parallel branch runs vector+keyword search for its sub-question."""
    from backend.agents.retrieval_node import RetrievalBranchAgent

    state = {
        "query": "droits du salarié licencié",
        "branch_query": "préavis de licenciement",
        "branch_index": 0,
        "tasks": [],
    }
    result = await RetrievalBranchAgent().run(state, seeded_ctx)
    assert result["branch_evidence"], "branch should return evidence for a seeded topic"
    assert result["branch_trace"][0].startswith("retrieval_branch 1:")
    # No concurrent-channel violations: branches only touch additive channels.
    assert set(result) == {"branch_evidence", "branch_trace"}


async def test_retrieval_merge_fuses_branches_and_counts_retry(seeded_ctx):
    from backend.agents.retrieval_node import RetrievalMergeAgent

    chunks = [
        EvidenceChunk(content="preuve A"),
        EvidenceChunk(content="preuve B"),
        EvidenceChunk(content="preuve A"),  # duplicate content, different id
    ]
    state = {
        "query": "q",
        "trace": [],
        "evidence": [],
        "branch_evidence": chunks,
        "branch_trace": ["retrieval_branch 1: 'a' -> 2 chunks", "retrieval_branch 2: 'b' -> 1 chunks"],
        "needs_more_retrieval": True,
        "retrieval_retries": 0,
    }
    result = await RetrievalMergeAgent().run(state, seeded_ctx)
    assert len(result["evidence"]) == 3
    assert result["retrieval_retries"] == 1
    assert result["needs_more_retrieval"] is False
    trace = "\n".join(result["trace"])
    assert "retrieval_branch 1" in trace and "retrieval_branch 2" in trace
    assert "parallel branch(es)" in trace


def test_merge_branch_chunks_reducer_dedups():
    from backend.core.state import merge_branch_chunks

    a = EvidenceChunk(content="A")
    b = EvidenceChunk(content="B")
    merged = merge_branch_chunks([a], [a, b])
    assert {c.chunk_id for c in merged} == {a.chunk_id, b.chunk_id}
    # Retry pass: re-adding the same chunks must not double them.
    again = merge_branch_chunks(merged, [a, b])
    assert len(again) == 2


async def test_rerank_llm_hook_blends_scores():
    """The LLM rescore hook blends 50/50 with the heuristic score."""
    from backend.core.embeddings import HashEmbeddings
    from backend.retrieval.reranker import rerank

    class _ScoreLLM:
        async def complete(self, system, user, **kwargs):
            count = user.count("[")
            return "[" + ", ".join(["0.95"] * count) + "]"

    chunks = [_chunk("licenciement préavis indemnité"), _chunk("autre contenu hors sujet")]
    ranked = await rerank("préavis licenciement", chunks, top_k=2, llm=_ScoreLLM(), embedder=HashEmbeddings())
    assert len(ranked) == 2
    # LLM gave 0.95 everywhere: every chunk's score is lifted above the pure
    # heuristic floor of the off-topic chunk.
    assert all(c.rerank_score > 0.4 for c in ranked)


async def test_coordinator_passes_llm_to_rerank_when_enabled(seeded_ctx, settings):
    """Coordinator wires ctx.llm into rerank when rerank_llm_enabled is True."""
    import backend.retrieval.coordinator as coord_module

    calls = []

    class _RecordingLLM:
        async def complete(self, system, user, **kwargs):
            calls.append(user)
            return "[0.5]"

    seeded_ctx.llm = _RecordingLLM()
    settings.rerank_llm_enabled = True
    seeded_ctx.settings = settings
    coordinator = coord_module.RetrievalCoordinator(seeded_ctx)
    tasks = [SearchTask(kind=SearchKind.VECTOR, query="préavis licenciement", top_k=4)]
    results = await coordinator.retrieve(tasks)
    assert results
    assert calls, "expected the rerank LLM hook to be called"


async def test_coordinator_skips_llm_rerank_when_disabled(seeded_ctx, settings):
    import backend.retrieval.coordinator as coord_module

    calls = []

    class _RecordingLLM:
        async def complete(self, system, user, **kwargs):
            calls.append(user)
            return "[0.5]"

    seeded_ctx.llm = _RecordingLLM()
    settings.rerank_llm_enabled = False
    seeded_ctx.settings = settings
    coordinator = coord_module.RetrievalCoordinator(seeded_ctx)
    tasks = [SearchTask(kind=SearchKind.VECTOR, query="préavis licenciement", top_k=4)]
    await coordinator.retrieve(tasks)
    assert not calls
