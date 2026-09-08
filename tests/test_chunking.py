"""Ingestion chunking tests (offline, tmp_path only)."""

from __future__ import annotations

import inspect

_ARTICLE_TEXT = (
    "Article 1\n"
    "Le Burkina Faso est une République démocratique, une et indivisible.\n\n"
    "Article 2\n"
    "La République est laïque et garantit la liberté de conscience.\n\n"
    "Article 3\n"
    "La souveraineté nationale appartient au peuple burkinabè tout entier."
)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _document(text: str, name: str = "Constitution du Burkina Faso"):
    from backend.ingestion.loaders import ExtractedDocument

    return ExtractedDocument(name=name, text=text, pages=[text], metadata={})


async def test_semantic_chunk_splits_on_article_boundaries():
    from backend.ingestion.chunking import semantic_chunk

    doc = _document(_ARTICLE_TEXT)
    chunks = await _maybe_await(semantic_chunk(doc, document_id="constitution-test"))
    assert len(chunks) >= 3
    articles = [str(c.article) for c in chunks if c.article is not None]
    assert any("1" in a for a in articles)
    assert any("2" in a for a in articles)
    # each chunk keeps the document name so evidence stays traceable
    assert all(c.document_name == "Constitution du Burkina Faso" for c in chunks)


async def test_parent_child_chunking_sets_parent_chunk_id():
    from backend.ingestion.chunking import parent_child_chunk

    doc = _document(_ARTICLE_TEXT, name="Code du travail")
    chunks = await _maybe_await(parent_child_chunk(doc, document_id="ct-test"))
    children = [c for c in chunks if c.parent_chunk_id]
    assert children, "expected at least one child chunk with parent_chunk_id set"


_LONG_ARTICLE_TEXT = (
    "Article 1\n"
    + "\n".join(f"Alinéa {i} : " + "disposition légale " * 10 for i in range(1, 17))
    + "\n\nArticle 2\nTexte court."
)


async def test_legal_children_split_on_alinea_boundaries():
    from backend.ingestion.chunking import legal_parent_child_chunk

    doc = _document(_LONG_ARTICLE_TEXT, name="Code pénal")
    chunks = await _maybe_await(legal_parent_child_chunk(doc, document_id="cp-test"))
    children = [c for c in chunks if c.parent_chunk_id and c.article == "1"]
    # article 1 is longer than the default child size: it must produce several
    # children, each made of whole alinéas (never a mid-line cut)
    assert len(children) > 1
    for child in children:
        for line in child.content.splitlines():
            assert line.startswith(("Article 1", "Alinéa")), f"mid-alinéa cut: {line!r}"


async def test_legal_short_article_stays_a_single_child():
    from backend.ingestion.chunking import legal_parent_child_chunk

    doc = _document(_LONG_ARTICLE_TEXT, name="Code pénal")
    chunks = await _maybe_await(legal_parent_child_chunk(doc, document_id="cp-test"))
    children = [c for c in chunks if c.parent_chunk_id and c.article == "2"]
    assert len(children) == 1
    assert children[0].content == "Article 2\nTexte court."


_HEADING_TEXT = (
    "Livre IV\n"
    "Des crimes internationaux.\n\n"
    "Titre I\n"
    "Du génocide.\n\n"
    "Article 1\n"
    "Le génocide est puni de la réclusion criminelle à perpétuité.\n\n"
    "Article 2\n"
    "La complicité est punie de la même peine."
)


async def test_heading_segments_get_heading_role_not_child():
    from backend.ingestion.chunking import legal_parent_child_chunk

    doc = _document(_HEADING_TEXT, name="Code pénal")
    chunks = await _maybe_await(legal_parent_child_chunk(doc, document_id="cp-headings"))
    headings = [c for c in chunks if c.article is None]
    assert headings, "expected bare heading segments (Livre IV, Titre I)"
    # excluded from retrieval (dense role="child" filter + BM25 corpus), but
    # kept in the store with their structure metadata for browsing
    assert all(c.metadata.get("role") == "heading" for c in headings)
    assert any(c.hierarchy == {"livre": "IV"} for c in headings)
    assert any(c.hierarchy == {"livre": "IV", "titre": "I"} for c in headings)
    # article segments keep the parent/child roles retrieval relies on
    article_chunks = [c for c in chunks if c.article is not None]
    assert {c.metadata.get("role") for c in article_chunks} == {"parent", "child"}


async def test_article_children_carry_contextual_retrieval_text():
    from backend.ingestion.chunking import legal_parent_child_chunk

    doc = _document(_HEADING_TEXT, name="Code pénal")
    chunks = await _maybe_await(
        legal_parent_child_chunk(doc, document_id="cp-prefix", law_number="025-2018/AN")
    )
    children = [c for c in chunks if c.metadata.get("role") == "child"]
    assert children
    for child in children:
        assert child.retrieval_text is not None
        prefix = child.retrieval_text.splitlines()[0]
        assert prefix.startswith("« Code pénal (025-2018/AN) — Livre IV > Titre I — Article ")
        assert prefix.endswith(". »")
        assert child.retrieval_text.endswith(child.content)
        # content stays raw: display/citation text keeps no prefix
        assert not child.content.startswith("«")
    # heading chunks carry no retrieval_text: raw content serves both roles
    assert all(c.retrieval_text is None for c in chunks if c.metadata.get("role") == "heading")


async def test_retrieval_text_prefix_omits_empty_parts():
    from backend.ingestion.chunking import legal_parent_child_chunk

    doc = _document("Article 1\nTexte intégral.", name="Loi simple")
    chunks = await _maybe_await(legal_parent_child_chunk(doc, document_id="loi-1"))
    child = next(c for c in chunks if c.metadata.get("role") == "child")
    # no law number, no hierarchy: only the parts that exist are rendered
    assert child.retrieval_text == f"« Loi simple — Article 1. »\n{child.content}"
