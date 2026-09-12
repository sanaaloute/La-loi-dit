"""Tests for the Phase-2 direct article lookup.

- ``LegalGraphStore.find_articles``: cross-document exact article resolution
  (bare "article N" mentions, capped).
- ``GraphWorker.run``: bare article numbers resolve across documents (score
  0.9, ``retrieved_via="graph"``).
- ``RetrievalCoordinator.retrieve``: the always-on mention lookup injects
  graph hits even when the planner did not schedule a GRAPH task.
"""

from __future__ import annotations

import pytest

from backend.core.models import EvidenceChunk, SearchKind, SearchTask
from backend.knowledge.models import LegalArticleRecord, LegalDocumentRecord
from backend.knowledge.store import LegalGraphStore
from backend.retrieval.coordinator import RetrievalCoordinator
from backend.retrieval.graph_worker import GraphWorker


@pytest.fixture
async def store(settings):
    s = LegalGraphStore(settings)
    yield s
    await s.close()


def _article_chunk(document_id: str, article: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        document_id=document_id,
        document_name=document_id,
        content=text,
        article=article,
        metadata={"role": "child"},
    )


async def _seed_two_codes_with_article_244(store: LegalGraphStore) -> None:
    for doc_id, name in (
        ("code-assurances", "Code des assurances du Burkina Faso"),
        ("code-penal", "Code pénal du Burkina Faso"),
    ):
        await store.upsert_document(LegalDocumentRecord(document_id=doc_id, name=name))
        await store.upsert_articles(
            doc_id,
            [LegalArticleRecord(document_id=doc_id, article="244", text_preview="...")],
        )


async def test_find_documents_matches_filename_style_names(store):
    """Ingested documents often keep raw filenames: the hint matching must
    survive separators and extensions ("cima_code-des-assurances_2019.pdf"
    must match the hint "code des assurances")."""
    await store.upsert_document(
        LegalDocumentRecord(document_id="cima", name="cima_code-des-assurances_2019.pdf")
    )
    found = await store.find_documents(name_hint="code des assurances")
    assert [d.document_id for d in found] == ["cima"]
    # A hint with no token overlap must not match.
    assert await store.find_documents(name_hint="code pénal") == []


async def test_find_articles_resolves_across_documents(store):
    await _seed_two_codes_with_article_244(store)
    found = await store.find_articles("244")
    assert {r.document_id for r in found} == {"code-assurances", "code-penal"}
    # The cap bounds the ambiguity for very common numbers.
    capped = await store.find_articles("244", limit=1)
    assert len(capped) == 1
    assert await store.find_articles("9999") == []


async def test_graph_worker_resolves_bare_article(ctx, store):
    await _seed_two_codes_with_article_244(store)
    ctx.extras["legal_graph"] = store
    chunks = [
        _article_chunk("code-assurances", "244", "L'assureur avise la victime..."),
        _article_chunk("code-penal", "244", "Est puni quiconque..."),
        _article_chunk("code-penal", "1", "Hors sujet."),
    ]
    vectors = await ctx.embedder.embed([c.content for c in chunks])
    await ctx.vector_store.upsert(chunks, vectors)

    hits = await GraphWorker(ctx).run(
        SearchTask(kind=SearchKind.GRAPH, query="que dit l'article 244 ?", top_k=8)
    )
    assert hits, "bare article mention should resolve"
    assert {c.document_id for c in hits} == {"code-assurances", "code-penal"}
    assert all(c.article == "244" for c in hits)
    assert all(c.metadata.get("retrieved_via") == "graph" for c in hits)
    assert all(c.retrieval_score == pytest.approx(0.9) for c in hits)


async def test_coordinator_lookup_runs_without_planned_graph_task(ctx, store):
    await _seed_two_codes_with_article_244(store)
    ctx.extras["legal_graph"] = store
    chunks = [
        _article_chunk("code-assurances", "244", "L'assureur avise la victime..."),
        _article_chunk("code-assurances", "1", "Dispositions générales."),
    ]
    vectors = await ctx.embedder.embed([c.content for c in chunks])
    await ctx.vector_store.upsert(chunks, vectors)

    coordinator = RetrievalCoordinator(ctx)
    tasks = [
        SearchTask(
            kind=SearchKind.VECTOR,
            query="article 244 du code des assurances",
            top_k=ctx.settings.default_top_k,
        )
    ]
    results = await coordinator.retrieve(tasks)
    graph_hits = [c for c in results if c.metadata.get("retrieved_via") == "graph"]
    assert graph_hits, "always-on mention lookup should inject graph evidence"
    assert all(c.article == "244" for c in graph_hits)
    assert all(c.document_id == "code-assurances" for c in graph_hits)
