"""Legal knowledge graph tests: store CRUD, extraction, ingestion hook, graph worker.

Fully offline: SQLite tmp files, in-memory vector store, hash embeddings.
"""

from __future__ import annotations

import pytest

from backend.core.models import EvidenceChunk, SearchKind, SearchTask
from backend.knowledge.extraction import (
    extract_from_chunks,
    extract_query_mentions,
    extract_relationships,
)
from backend.knowledge.models import (
    ExtractedRelationship,
    LegalArticleRecord,
    LegalDocumentRecord,
    RelationType,
)
from backend.knowledge.store import LegalGraphStore, graph_store_for


def _chunk(
    content: str,
    *,
    document_id: str = "doc1",
    article: str | None = None,
    **kwargs,
) -> EvidenceChunk:
    return EvidenceChunk(document_id=document_id, content=content, article=article, **kwargs)


# ----------------------------------------------------------------------
# Store CRUD round-trip (SQLite tmp)
# ----------------------------------------------------------------------


@pytest.fixture
async def store(settings):
    s = LegalGraphStore(settings)
    yield s
    await s.close()


async def test_document_upsert_and_get(store):
    await store.upsert_document(
        LegalDocumentRecord(
            document_id="dct",
            name="Code du travail du Burkina Faso (Loi 028-2008/AN)",
            document_type="code",
            law_number="028-2008/AN",
            jurisdiction="Burkina Faso",
            status="active",
            issuing_authority="Assemblée nationale",
            authority="law",
            publication_date="2008-05-13",
            effective_date="2008-05-13",
            source_url="https://example.gov.bf/ct",
            version=1,
            content_hash="abc123",
        )
    )
    doc = await store.get_document("dct")
    assert doc is not None
    assert doc.name.startswith("Code du travail")
    assert doc.law_number == "028-2008/AN"
    assert doc.issuing_authority == "Assemblée nationale"
    assert doc.content_hash == "abc123"

    # Upsert replaces (new version) without duplicating.
    await store.upsert_document(
        LegalDocumentRecord(document_id="dct", name="Code du travail", version=2, content_hash="def456")
    )
    updated = await store.get_document("dct")
    assert updated is not None
    assert updated.version == 2
    assert updated.content_hash == "def456"
    assert await store.get_document("missing") is None


async def test_articles_roundtrip(store):
    await store.upsert_document(LegalDocumentRecord(document_id="dct", name="Code du travail"))
    await store.upsert_articles(
        "dct",
        [
            LegalArticleRecord(
                document_id="dct",
                article="340",
                section="Licenciement",
                hierarchy={"titre": "II", "chapitre": "3"},
                page=12,
                text_preview="Le préavis...",
                status="active",
                valid_from="2008-05-13",
            ),
            LegalArticleRecord(document_id="dct", article="341", text_preview="La faute lourde..."),
        ],
    )
    articles = await store.articles_of("dct")
    assert [a.article for a in articles] == ["340", "341"]
    assert articles[0].hierarchy == {"titre": "II", "chapitre": "3"}
    assert articles[0].page == 12
    only = await store.articles_of("dct", article="341")
    assert len(only) == 1 and only[0].article == "341"
    # Re-upsert replaces the set (new document version).
    await store.upsert_articles("dct", [LegalArticleRecord(document_id="dct", article="1")])
    assert [a.article for a in await store.articles_of("dct")] == ["1"]


async def test_relationships_roundtrip_and_dedupe(store):
    edges = [
        ExtractedRelationship(
            src_document="dct",
            relation=RelationType.CONTAINS,
            dst_document="dct",
            dst_article="341",
        ),
        ExtractedRelationship(
            src_document="dct",
            src_article="341",
            relation=RelationType.REFERENCES,
            dst_document="dct",
            dst_article="340",
        ),
        ExtractedRelationship(
            src_document="loi-2020",
            src_article="3",
            relation=RelationType.AMENDS,
            dst_document="dct",
            dst_article="341",
            dst_free_text=None,
            extracted_by="manual",
        ),
        ExtractedRelationship(
            src_document="dct",
            relation=RelationType.REFERENCES,
            dst_article="5",
            dst_free_text="Code pénal",
        ),
    ]
    added = await store.add_relationships(edges)
    assert added == 4
    # Exact duplicates are skipped on re-add.
    assert await store.add_relationships(edges) == 0

    # Source side.
    outgoing = await store.relationships_for("dct", "341")
    assert any(r.relation == RelationType.REFERENCES and r.dst_article == "340" for r in outgoing)
    # Destination side (answers "referenced/amended by whom?" without inverse rows).
    incoming = await store.relationships_for("dct", "341")
    assert any(r.relation == RelationType.AMENDS and r.src_document == "loi-2020" for r in incoming)
    # Document-level listing sees edges in both directions.
    doc_level = await store.relationships_for("dct")
    assert len(doc_level) >= 4
    # Unresolved target keeps its free text.
    free = [r for r in doc_level if r.dst_free_text == "Code pénal"]
    assert free and free[0].dst_document is None


async def test_find_documents_by_name_or_law_number(store):
    await store.upsert_document(
        LegalDocumentRecord(
            document_id="dct",
            name="Code du travail du Burkina Faso (Loi 028-2008/AN)",
            law_number="028-2008/AN",
        )
    )
    await store.upsert_document(LegalDocumentRecord(document_id="cp", name="Code pénal"))

    by_name = await store.find_documents(name_hint="code du travail")
    assert [d.document_id for d in by_name] == ["dct"]
    # Accents/case are normalized.
    by_name_ascii = await store.find_documents(name_hint="code penal")
    assert [d.document_id for d in by_name_ascii] == ["cp"]
    by_law = await store.find_documents(law_number="028-2008/AN")
    assert [d.document_id for d in by_law] == ["dct"]
    assert await store.find_documents(name_hint="constitution") == []


# ----------------------------------------------------------------------
# Extraction (precision-first)
# ----------------------------------------------------------------------


def test_extract_contains_issued_by_applies_to():
    chunk = _chunk(
        "Tout travailleur a droit à un salaire équitable.",
        article="1",
        issuing_authority="Assemblée nationale",
        metadata={"legal_domains": ["travail"]},
    )
    rels = extract_relationships(chunk)
    by_relation = {}
    for r in rels:
        by_relation.setdefault(r.relation, []).append(r)
    assert by_relation[RelationType.CONTAINS][0].dst_article == "1"
    assert by_relation[RelationType.ISSUED_BY][0].dst_free_text == "Assemblée nationale"
    assert by_relation[RelationType.APPLIES_TO][0].dst_free_text == "travail"


def test_extract_bare_reference_resolves_to_same_document():
    chunk = _chunk("Conformément à l'article 12, le contrat peut être rompu.", article="5")
    refs = [r for r in extract_relationships(chunk) if r.relation == RelationType.REFERENCES]
    assert len(refs) == 1
    assert refs[0].src_article == "5"
    assert refs[0].dst_document == "doc1"
    assert refs[0].dst_article == "12"
    assert refs[0].dst_free_text is None


def test_extract_qualified_reference_goes_to_free_text():
    chunk = _chunk("Au sens de l'article 5 du Code pénal, la faute est constituée.", article="9")
    refs = [r for r in extract_relationships(chunk) if r.relation == RelationType.REFERENCES]
    assert len(refs) == 1
    assert refs[0].dst_document is None
    assert refs[0].dst_article == "5"
    assert "Code pénal" in (refs[0].dst_free_text or "")


def test_extract_reference_with_law_number_target():
    chunk = _chunk(
        "Selon l'article 34 de la loi n° 028-2008/AN du 13 mai 2008, le préavis est dû.",
        article=None,
    )
    refs = [r for r in extract_relationships(chunk) if r.relation == RelationType.REFERENCES]
    assert len(refs) == 1
    assert refs[0].dst_article == "34"
    assert refs[0].dst_free_text == "loi n° 028-2008/AN"


def test_extract_amends_and_repeals():
    chunk = _chunk(
        "La présente loi modifie l'article 34 de la loi n° 028-2008/AN. "
        "Elle abroge l'article 12 de la loi n° 010-99/AN.",
        article="3",
    )
    rels = extract_relationships(chunk)
    amends = [r for r in rels if r.relation == RelationType.AMENDS]
    repeals = [r for r in rels if r.relation == RelationType.REPEALS]
    assert len(amends) == 1 and amends[0].dst_article == "34"
    assert "028-2008/AN" in (amends[0].dst_free_text or "")
    assert len(repeals) == 1 and repeals[0].dst_article == "12"
    assert "010-99/AN" in (repeals[0].dst_free_text or "")
    # Amendment targets are not double-counted as plain references.
    assert not [r for r in rels if r.relation == RelationType.REFERENCES]


def test_extract_amends_whole_law():
    chunk = _chunk("Le présent décret modifie la loi n° 028-2008/AN.", article="1")
    amends = [r for r in extract_relationships(chunk) if r.relation == RelationType.AMENDS]
    assert len(amends) == 1
    assert amends[0].dst_article is None
    assert amends[0].dst_free_text == "loi n° 028-2008/AN"


def test_extract_article_l_number():
    chunk = _chunk("Conformément à l'article L. 234-1, la société est immatriculée.", article="7")
    refs = [r for r in extract_relationships(chunk) if r.relation == RelationType.REFERENCES]
    assert len(refs) == 1
    assert refs[0].dst_article == "L.234-1"


def test_extract_negative_cases_no_numbers():
    # No article/law numbers at all -> no edges (article-less chunk).
    assert extract_relationships(_chunk("Le délai de préavis est important pour les salariés.")) == []
    # The word "article" without a number is not an edge.
    assert extract_relationships(_chunk("Le présent article prévoit une sanction.", article="4")) == [] or all(
        r.relation == RelationType.CONTAINS
        for r in extract_relationships(_chunk("Le présent article prévoit une sanction.", article="4"))
    )
    # A restatement of the chunk's own article is not a self-edge.
    chunk = _chunk("L'article 4 s'applique aux contrats en cours.", article="4")
    assert not [r for r in extract_relationships(chunk) if r.relation == RelationType.REFERENCES]
    # Vague amendment language without any number is skipped.
    chunk = _chunk("La présente loi modifie les dispositions antérieures.", article="1")
    assert all(
        r.relation in (RelationType.CONTAINS,) for r in extract_relationships(chunk)
    )


def test_extract_from_chunks_dedupes():
    chunks = [
        _chunk("Conformément à l'article 12, le contrat peut être rompu.", article="5"),
        _chunk("Conformément à l'article 12, le contrat peut être rompu.", article="5"),
    ]
    rels = extract_from_chunks(chunks)
    assert len(rels) == len({r.dedup_key() for r in rels})


def test_extract_query_mentions():
    mentions = extract_query_mentions("Que dit l'article 341 du code du travail ?")
    assert mentions[0].article == "341"
    assert mentions[0].document_hint == "code du travail"
    law = extract_query_mentions("Qu'en dit la loi n° 028-2008/AN ?")
    assert any(m.law_number == "028-2008/AN" for m in law)
    assert extract_query_mentions("quels sont mes droits ?") == []


# ----------------------------------------------------------------------
# Ingestion hook
# ----------------------------------------------------------------------

_LEGAL_TEXT = (
    "Article 1: Tout travailleur a droit à un salaire équitable pour un travail égal.\n\n"
    "Article 2: Conformément à l'article 1, le salaire est fixé par convention collective "
    "ou par accord d'entreprise.\n\n"
    "Article 3: Le présent code abroge l'article 12 de la loi n° 010-99/AN."
)


async def test_ingestion_populates_legal_graph(ctx):
    from backend.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(ctx)
    result = await pipeline.ingest_text("code-travail-graph-test.txt", _LEGAL_TEXT)
    assert result.status == "indexed"

    store = graph_store_for(ctx)
    assert store is not None
    doc = await store.get_document(result.document_id)
    assert doc is not None
    assert doc.content_hash

    articles = await store.articles_of(result.document_id)
    assert {"1", "2", "3"} <= {a.article for a in articles}

    rels = await store.relationships_for(result.document_id)
    relations = {(r.relation, r.src_article, r.dst_article) for r in rels}
    # Containment for every article.
    assert (RelationType.CONTAINS, None, "1") in relations
    # In-text cross-reference article 2 -> article 1 (same document).
    assert (RelationType.REFERENCES, "2", "1") in relations
    # Active repeal language with explicit law number.
    repeals = [r for r in rels if r.relation == RelationType.REPEALS]
    assert repeals and repeals[0].dst_article == "12"
    assert "010-99/AN" in (repeals[0].dst_free_text or "")


async def test_ingestion_graph_failure_never_fails_ingestion(ctx, monkeypatch):
    from backend.ingestion.pipeline import IngestionPipeline

    import backend.knowledge.store as store_module

    def _boom(_ctx):
        raise RuntimeError("graph store exploded")

    monkeypatch.setattr(store_module, "graph_store_for", _boom)
    pipeline = IngestionPipeline(ctx)
    result = await pipeline.ingest_text("code-travail-graph-fail.txt", _LEGAL_TEXT)
    assert result.status == "indexed"
    assert result.chunks_created > 0


async def test_ingestion_graph_disabled_flag_is_noop(ctx):
    ctx.settings.legal_graph_enabled = False
    assert graph_store_for(ctx) is None

    from backend.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(ctx)
    result = await pipeline.ingest_text("code-travail-graph-off.txt", _LEGAL_TEXT)
    assert result.status == "indexed"
    assert ctx.extras.get("legal_graph") is None  # store never even created


# ----------------------------------------------------------------------
# Graph worker: lookup + expansion
# ----------------------------------------------------------------------


async def _seed_graph_corpus(ctx):
    """Two articles of a fictional labour code, with a 341 -> 340 reference."""
    store = graph_store_for(ctx)
    assert store is not None
    await store.upsert_document(
        LegalDocumentRecord(
            document_id="dct",
            name="Code du travail du Burkina Faso (Loi 028-2008/AN)",
            law_number="028-2008/AN",
        )
    )
    await store.upsert_articles(
        "dct",
        [
            LegalArticleRecord(document_id="dct", article="340"),
            LegalArticleRecord(document_id="dct", article="341"),
        ],
    )
    await store.add_relationships(
        [
            ExtractedRelationship(
                src_document="dct",
                src_article="341",
                relation=RelationType.REFERENCES,
                dst_document="dct",
                dst_article="340",
            )
        ]
    )
    chunk_340 = EvidenceChunk(
        document_id="dct",
        document_name="Code du travail du Burkina Faso",
        article="340",
        content="Le préavis de licenciement est d'un mois pour les employés.",
    )
    chunk_341 = EvidenceChunk(
        document_id="dct",
        document_name="Code du travail du Burkina Faso",
        article="341",
        content="La faute lourde prive le salarié du préavis de licenciement.",
    )
    vectors = await ctx.embedder.embed([c.content for c in (chunk_340, chunk_341)])
    await ctx.vector_store.upsert([chunk_340, chunk_341], vectors)
    return chunk_340, chunk_341


async def test_graph_worker_registered(ctx):
    from backend.retrieval.graph_worker import GraphWorker
    from backend.retrieval.workers import worker_for

    assert isinstance(worker_for(SearchKind.GRAPH, ctx), GraphWorker)


async def test_graph_worker_lookup_by_article_and_document(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    _, chunk_341 = await _seed_graph_corpus(ctx)
    worker = GraphWorker(ctx)
    results = await worker.run(
        SearchTask(kind=SearchKind.GRAPH, query="Que dit l'article 341 du code du travail ?")
    )
    assert results
    assert all(c.document_id == "dct" and c.article == "341" for c in results)
    assert chunk_341.chunk_id in {c.chunk_id for c in results}
    assert all(c.metadata.get("retrieved_via") == "graph" for c in results)


async def test_graph_worker_lookup_by_law_number(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    await _seed_graph_corpus(ctx)
    worker = GraphWorker(ctx)
    results = await worker.run(
        SearchTask(kind=SearchKind.GRAPH, query="Que prévoit la loi n° 028-2008/AN ?", top_k=5)
    )
    assert {c.article for c in results} == {"340", "341"}


async def test_graph_worker_no_mention_no_results(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    await _seed_graph_corpus(ctx)
    worker = GraphWorker(ctx)
    assert await worker.run(SearchTask(kind=SearchKind.GRAPH, query="quels sont mes droits ?")) == []


async def test_graph_worker_expansion_adds_related_articles_low_score(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    chunk_340, chunk_341 = await _seed_graph_corpus(ctx)
    worker = GraphWorker(ctx)
    expanded = await worker.expand([chunk_341])
    assert len(expanded) == 2
    added = [c for c in expanded if c.chunk_id == chunk_340.chunk_id]
    assert added, "referenced article 340 should be pulled in"
    assert added[0].metadata.get("retrieved_via") == "graph"
    assert added[0].retrieval_score < 0.1  # low-score candidate
    # Input chunk is untouched and comes first.
    assert expanded[0].chunk_id == chunk_341.chunk_id


async def test_graph_worker_disabled_flag_is_noop(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    _, chunk_341 = await _seed_graph_corpus(ctx)
    ctx.settings.legal_graph_enabled = False
    worker = GraphWorker(ctx)
    assert await worker.run(
        SearchTask(kind=SearchKind.GRAPH, query="Que dit l'article 341 du code du travail ?")
    ) == []
    assert await worker.expand([chunk_341]) == [chunk_341]


async def test_graph_worker_without_store_never_raises(ctx):
    from backend.retrieval.graph_worker import GraphWorker

    worker = GraphWorker(ctx)
    # No graph data at all: empty results, no exception.
    assert await worker.run(
        SearchTask(kind=SearchKind.GRAPH, query="article 1 de la constitution")
    ) == []
    chunk = EvidenceChunk(document_id="nope", article="1", content="x")
    assert await worker.expand([chunk]) == [chunk]
