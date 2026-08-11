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
    + "\n".join(f"Alinéa {i} : " + "disposition légale " * 10 for i in range(1, 9))
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
