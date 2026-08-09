"""Chunk hierarchy tests: heading levels land in ``chunk.hierarchy`` (spec §8)."""

from __future__ import annotations

import inspect

_STRUCTURED_TEXT = (
    "Livre I\n"
    "Dispositions générales.\n\n"
    "Titre II\n"
    "Des personnes.\n\n"
    "Chapitre 1\n"
    "De l'état civil.\n\n"
    "Section 3\n"
    "Des naissances.\n\n"
    "Article 1er\n"
    "Tout citoyen jouit des droits civils.\n\n"
    "Article 2\n"
    "Nul ne peut être privé de ses droits.\n\n"
    "Chapitre 2\n"
    "Des actes.\n\n"
    "Article 3\n"
    "Les actes sont signés.\n\n"
    "Annexe I\n"
    "Formulaire type.\n"
)

_ORDINAL_TEXT = (
    "Article premier\n"
    "La présente loi régit les personnes.\n\n"
    "Article 2\n"
    "Elle entre en vigueur à sa publication."
)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _document(text: str, name: str = "Code civil test"):
    from backend.ingestion.loaders import ExtractedDocument

    return ExtractedDocument(name=name, text=text, pages=[text], metadata={})


def _by_article(chunks, article: str):
    return [c for c in chunks if c.article == article]


async def test_semantic_chunk_tracks_each_heading_level_in_hierarchy():
    from backend.ingestion.chunking import semantic_chunk

    chunks = await _maybe_await(semantic_chunk(_document(_STRUCTURED_TEXT), "doc-1"))
    article1 = _by_article(chunks, "1")
    assert article1
    assert article1[0].hierarchy == {
        "livre": "I",
        "titre": "II",
        "chapitre": "1",
        "section": "3",
    }
    # compat: section stays the deepest heading string
    assert article1[0].section == "Section 3"


async def test_deeper_levels_reset_when_a_higher_heading_appears():
    from backend.ingestion.chunking import semantic_chunk

    chunks = await _maybe_await(semantic_chunk(_document(_STRUCTURED_TEXT), "doc-1"))
    article3 = _by_article(chunks, "3")
    assert article3
    # "Chapitre 2" drops the "section" level from the hierarchy.
    assert article3[0].hierarchy == {"livre": "I", "titre": "II", "chapitre": "2"}
    assert article3[0].section == "Chapitre 2"


async def test_article_1er_and_article_premier_normalize_to_1():
    from backend.ingestion.chunking import semantic_chunk

    chunks = await _maybe_await(semantic_chunk(_document(_STRUCTURED_TEXT), "doc-1"))
    assert _by_article(chunks, "1"), "Article 1er must be detected and normalized to '1'"

    ordinal_chunks = await _maybe_await(semantic_chunk(_document(_ORDINAL_TEXT), "doc-2"))
    assert _by_article(ordinal_chunks, "1"), "Article premier must map to article '1'"
    assert _by_article(ordinal_chunks, "2")


async def test_annexe_is_a_boundary_and_replaces_the_hierarchy():
    from backend.ingestion.chunking import semantic_chunk

    chunks = await _maybe_await(semantic_chunk(_document(_STRUCTURED_TEXT), "doc-1"))
    annexe = [c for c in chunks if c.hierarchy.get("annexe")]
    assert annexe, "Annexe I must open its own segment"
    assert annexe[0].hierarchy == {"annexe": "I"}
    assert annexe[0].section == "Annexe I"


async def test_heading_chunks_carry_their_own_hierarchy():
    from backend.ingestion.chunking import semantic_chunk

    chunks = await _maybe_await(semantic_chunk(_document(_STRUCTURED_TEXT), "doc-1"))
    chapitre1 = [c for c in chunks if c.section == "Chapitre 1" and c.article is None]
    assert chapitre1
    assert chapitre1[0].hierarchy == {"livre": "I", "titre": "II", "chapitre": "1"}


async def test_legal_parent_child_chunk_stamps_hierarchy_on_parents_and_children():
    from backend.ingestion.chunking import legal_parent_child_chunk

    chunks = await _maybe_await(legal_parent_child_chunk(_document(_STRUCTURED_TEXT), "doc-1"))
    assert chunks
    article1 = _by_article(chunks, "1")
    assert article1
    assert all(
        c.hierarchy
        == {"livre": "I", "titre": "II", "chapitre": "1", "section": "3"}
        for c in article1
    )
    # children inherit the same hierarchy as their parent
    children = [c for c in article1 if c.parent_chunk_id]
    assert children
