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
