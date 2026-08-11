"""Parent expansion dual-text fields (spec §7).

When a retrieved child chunk is expanded to its parent, the parent carries
``retrieval_text`` (the child passage that matched) and ``context_text``
(the enclosing article/section).  Chunks without a parent keep both None —
their ``content`` serves both roles.
"""

from __future__ import annotations

from backend.agents.parent_expansion import parent_expansion_node
from backend.core.models import EvidenceChunk


async def _upsert(ctx, chunks: list[EvidenceChunk]) -> None:
    vectors = await ctx.embedder.embed([c.content for c in chunks])
    await ctx.vector_store.upsert(chunks, vectors)


async def test_expansion_stamps_retrieval_and_context_text(ctx):
    parent = EvidenceChunk(
        chunk_id="parent-1",
        document_id="doc-1",
        document_name="Code du travail",
        content="Article 12: Le préavis de licenciement est de un mois. Texte complet.",
        article="12",
    )
    child = EvidenceChunk(
        chunk_id="child-1",
        document_id="doc-1",
        document_name="Code du travail",
        content="Le préavis de licenciement est de un mois.",
        article="12",
        parent_chunk_id="parent-1",
    )
    await _upsert(ctx, [parent, child])

    state = {"evidence": [child], "trace": [], "errors": []}
    out = await parent_expansion_node(state, ctx)

    assert out["evidence"][0] is parent
    # What matched vs. the context it was expanded to.
    assert parent.retrieval_text == child.content
    assert parent.context_text == parent.content
    assert child in parent.child_chunks
    # The child itself is untouched: only expanded parents carry dual text.
    assert child.retrieval_text is None
    assert child.context_text is None


async def test_expansion_keeps_first_matching_child_retrieval_text(ctx):
    parent = EvidenceChunk(chunk_id="parent-2", document_id="doc-1", content="Article complet.")
    child_a = EvidenceChunk(
        chunk_id="child-a", document_id="doc-1", content="Passage A.", parent_chunk_id="parent-2"
    )
    child_b = EvidenceChunk(
        chunk_id="child-b", document_id="doc-1", content="Passage B.", parent_chunk_id="parent-2"
    )
    await _upsert(ctx, [parent, child_a, child_b])

    state = {"evidence": [child_a, child_b], "trace": [], "errors": []}
    out = await parent_expansion_node(state, ctx)

    # Both children expand to the same parent; it appears exactly once.
    assert out["evidence"] == [parent]
    assert parent.retrieval_text == "Passage A."  # first matching child wins
    assert parent.context_text == "Article complet."
    assert {c.chunk_id for c in parent.child_chunks} == {"child-a", "child-b"}


async def test_expanded_parent_inherits_best_child_scores(ctx):
    parent = EvidenceChunk(chunk_id="parent-3", document_id="doc-1", content="Article complet.")
    weak = EvidenceChunk(
        chunk_id="child-weak", document_id="doc-1", content="Passage faible.",
        parent_chunk_id="parent-3", retrieval_score=0.4, rerank_score=0.3,
    )
    strong = EvidenceChunk(
        chunk_id="child-strong", document_id="doc-1", content="Passage fort.",
        parent_chunk_id="parent-3", retrieval_score=0.9, rerank_score=0.8,
    )
    await _upsert(ctx, [parent, weak, strong])

    state = {"evidence": [weak, strong], "trace": [], "errors": []}
    out = await parent_expansion_node(state, ctx)

    assert out["evidence"] == [parent]
    # The parent displays the best attached child's scores, not 0.00.
    assert parent.retrieval_score == 0.9
    assert parent.rerank_score == 0.8
    assert parent.metadata["expansion"] == "parent"


async def test_chunks_without_parent_keep_dual_text_none(ctx):
    standalone = EvidenceChunk(chunk_id="solo-1", document_id="doc-2", content="Texte autonome.")
    await _upsert(ctx, [standalone])

    state = {"evidence": [standalone], "trace": [], "errors": []}
    out = await parent_expansion_node(state, ctx)

    assert out["evidence"] == [standalone]
    assert standalone.retrieval_text is None
    assert standalone.context_text is None
