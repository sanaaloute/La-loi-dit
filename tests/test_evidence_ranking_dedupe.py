"""Evidence ranking dedupe: one citation number per legal provision.

The index stores several chunks per article (overlapping windows, expanded
parents next to standalone hits).  Ranking must merge them so answers never
cite [9] and [10] for the same article.
"""

from __future__ import annotations

from backend.agents.evidence_ranking import EvidenceRankingAgent, _dedupe_same_source
from backend.core.models import AuthorityLevel, EvidenceChunk


def _chunk(**kwargs) -> EvidenceChunk:
    kwargs.setdefault("document_id", "doc-1")
    kwargs.setdefault("document_name", "Charte de la Transition du Burkina Faso (2015)")
    kwargs.setdefault("content", "contenu")
    kwargs.setdefault("retrieval_score", 0.8)
    kwargs.setdefault("rerank_score", 0.8)
    kwargs.setdefault("authority", AuthorityLevel.LAW)
    kwargs.setdefault("confidence", 0.8)
    return EvidenceChunk(**kwargs)


def test_same_article_chunks_merge_keeping_first():
    best = _chunk(article="168", content="article 168, fenêtre A")
    other = _chunk(article="168", content="article 168, fenêtre B")
    assert _dedupe_same_source([best, other]) == [best]


def test_article_normalization_merges_case_and_spacing():
    a = _chunk(article=" 168 ", content="fenêtre A")
    b = _chunk(article="168", content="fenêtre B")
    assert _dedupe_same_source([a, b]) == [a]


def test_distinct_articles_are_kept():
    a = _chunk(article="168", content="art 168")
    b = _chunk(article="169", content="art 169")
    assert _dedupe_same_source([a, b]) == [a, b]


def test_active_and_repealed_versions_never_merge():
    active = _chunk(article="168", status="active")
    repealed = _chunk(article="168", status="repealed")
    assert _dedupe_same_source([active, repealed]) == [active, repealed]


def test_article_less_chunks_merge_only_on_identical_content():
    a = _chunk(article=None, content="Titre 1 - Infractions")
    dup = _chunk(article=None, content="Titre 1 -   Infractions")  # whitespace variant
    other_section = _chunk(article=None, content="Titre 2 - Formalités")
    assert _dedupe_same_source([a, dup, other_section]) == [a, other_section]


async def test_ranking_agent_merges_duplicate_provisions(ctx):
    best = _chunk(article="168", content="fenêtre A", rerank_score=0.9)
    other = _chunk(article="168", content="fenêtre B", rerank_score=0.7)
    distinct = _chunk(article="1", content="article premier")
    state = {"evidence": [other, distinct, best], "trace": [], "plan": None}
    result = await EvidenceRankingAgent().run(state, ctx)
    ranked = result["ranked_evidence"]
    assert len(ranked) == 2
    assert ranked[0].article == "168"
    assert ranked[0].content == "fenêtre A"
    assert "1 duplicates merged" in result["trace"][-1]
