"""Vector store filter parity tests: promoted scalar fields (spec §11).

The in-memory store is tested directly; Milvus coverage is split between the
pure ``build_native_filter_expr`` helper (no server needed) and an optional
Milvus Lite round-trip that skips when pymilvus is unavailable.
"""

from __future__ import annotations

import pytest

from backend.core.models import DocumentType, EvidenceChunk
from backend.vectorstore.memory_store import InMemoryVectorStore, matches_filters
from backend.vectorstore.milvus_store import (
    _REQUIRED_FIELDS,
    build_native_filter_expr,
)


def _chunk(content: str, **kwargs) -> EvidenceChunk:
    return EvidenceChunk(content=content, **kwargs)


# ------------------------------------------------------- in-memory filtering


def test_matches_filters_supports_new_top_level_fields():
    law = _chunk(
        "loi",
        article="5",
        status="active",
        document_type=DocumentType.LAW,
    )
    repealed = _chunk("ancienne loi", article="5", status="repealed")

    assert matches_filters(law, {"article": "5"})
    assert matches_filters(law, {"status": "active"})
    assert matches_filters(law, {"document_type": "law"})
    assert matches_filters(law, {"document_type": DocumentType.LAW})
    assert matches_filters(law, {"status": ["active", "amended"]})

    assert not matches_filters(repealed, {"status": "active"})
    assert not matches_filters(law, {"article": "6"})
    # a chunk without the field set behaves like the native "" scalar
    assert not matches_filters(_chunk("vide"), {"article": "5"})
    # ...while the model default (status="active") matches, exactly like the
    # value Milvus upserts into its scalar column.
    assert matches_filters(_chunk("vide"), {"status": "active"})


async def test_in_memory_search_filters_on_promoted_fields():
    store = InMemoryVectorStore()
    chunks = [
        _chunk("droit du travail préavis", status="active", article="1",
               document_type=DocumentType.CODE),
        _chunk("droit du travail préavis", status="repealed", article="1",
               document_type=DocumentType.CODE),
        _chunk("droit du travail préavis", status="active", article="2",
               document_type=DocumentType.LAW),
    ]
    vectors = [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]]
    await store.upsert(chunks, vectors)

    active_only = await store.search([1.0, 0.0], top_k=10, filters={"status": "active"})
    assert {c.status for c in active_only} == {"active"}

    by_type = await store.search([1.0, 0.0], top_k=10, filters={"document_type": "code"})
    assert {c.document_type for c in by_type} == {DocumentType.CODE}

    combined = await store.search(
        [1.0, 0.0], top_k=10, filters={"status": "active", "article": "1"}
    )
    assert len(combined) == 1
    assert combined[0].article == "1" and combined[0].status == "active"


# ------------------------------------------------------ native expr building


def test_native_expr_covers_promoted_fields_only():
    expr, native = build_native_filter_expr(
        {
            "document_id": "doc-1",
            "article": "5",
            "status": "active",
            "document_type": "code",
            "legal_domains": ["labor_code"],
            "role": "child",
        }
    )
    assert native == {"document_id", "article", "status", "document_type", "legal_domains"}
    assert expr == (
        'document_id in ["doc-1"] and article in ["5"] '
        'and status in ["active"] and document_type in ["code"] '
        'and (array_contains_any(legal_domains, ["labor_code"]) '
        'or array_length(legal_domains) == 0)'
    )
    # non-promoted keys stay client-side
    assert "role" not in expr


def test_native_expr_legal_domains_semantics():
    # list of domains: ARRAY_CONTAINS_ANY + untagged chunks kept (array_length == 0)
    expr, native = build_native_filter_expr({"legal_domains": ["labor_code", "ohada_law"]})
    assert expr == (
        '(array_contains_any(legal_domains, ["labor_code", "ohada_law"]) '
        'or array_length(legal_domains) == 0)'
    )
    assert native == {"legal_domains"}
    # single value also accepted
    expr, _ = build_native_filter_expr({"legal_domains": "labor_code"})
    assert 'array_contains_any(legal_domains, ["labor_code"])' in expr
    # quote-unsafe values stay client-side
    expr, native = build_native_filter_expr({"legal_domains": ['x" or true --']})
    assert native == set() and expr is None


def test_native_expr_handles_lists_enums_and_empty():
    expr, native = build_native_filter_expr({"status": ["active", "amended"]})
    assert expr == 'status in ["active", "amended"]'
    assert native == {"status"}

    expr, native = build_native_filter_expr({"document_type": DocumentType.LAW})
    assert expr == 'document_type in ["law"]'

    assert build_native_filter_expr(None) == (None, set())
    assert build_native_filter_expr({}) == (None, set())
    # no promoted key => no expr, but no native key claimed either
    assert build_native_filter_expr({"role": "child"}) == (None, set())


def test_native_expr_rejects_quote_unsafe_values():
    expr, native = build_native_filter_expr({"article": '5" or true --', "status": "active"})
    # the unsafe value falls back to client-side filtering
    assert native == {"status"}
    assert expr == 'status in ["active"]'


def test_required_fields_include_promoted_scalars():
    assert {"document_id", "article", "status", "document_type", "legal_domains"} <= _REQUIRED_FIELDS


# --------------------------------------- optional Milvus Lite round-trip test


async def test_milvus_lite_native_filter_roundtrip(tmp_path, settings):
    pymilvus = pytest.importorskip("pymilvus")  # guarded: no server required
    from backend.vectorstore.milvus_store import MilvusVectorStore

    settings.milvus_uri = str(tmp_path / "filter_test.db")
    settings.milvus_collection = "filter_test"
    store = MilvusVectorStore(settings)
    await store.connect()
    try:
        chunks = [
            _chunk("droit du travail", document_id="d1", status="active",
                   article="1", document_type=DocumentType.CODE),
            _chunk("droit du travail", document_id="d2", status="repealed",
                   article="1", document_type=DocumentType.CODE),
        ]
        vectors = [[1.0] + [0.0] * (settings.embedding_dimension - 1)] * 2
        await store.upsert(chunks, vectors)

        active = await store.search(vectors[0], top_k=10, filters={"status": "active"})
        assert {c.status for c in active} == {"active"}
        assert all(c.document_id == "d1" for c in active)

        by_article = await store.search(
            vectors[0], top_k=10, filters={"article": "1", "document_type": "code"}
        )
        assert len(by_article) == 2
    finally:
        store._client.drop_collection(settings.milvus_collection)


async def test_milvus_lite_native_legal_domains_roundtrip(tmp_path, settings):
    pymilvus = pytest.importorskip("pymilvus")  # guarded: no server required
    from backend.vectorstore.milvus_store import MilvusVectorStore

    settings.milvus_uri = str(tmp_path / "domain_test.db")
    settings.milvus_collection = "domain_test"
    store = MilvusVectorStore(settings)
    await store.connect()
    try:
        labor = _chunk("préavis licenciement", document_id="d1",
                       metadata={"legal_domains": ["labor_code"]})
        commercial = _chunk("création sarl associés", document_id="d2",
                            metadata={"legal_domains": ["commercial_law"]})
        untagged = _chunk("document sans classification", document_id="d3", metadata={})
        chunks = [labor, commercial, untagged]
        vectors = [[1.0] + [0.0] * (settings.embedding_dimension - 1)] * 3
        await store.upsert(chunks, vectors)

        hits = await store.search(
            vectors[0], top_k=10, filters={"legal_domains": ["labor_code"]}
        )
        ids = {c.document_id for c in hits}
        assert "d1" in ids, "matching domain must be returned"
        assert "d3" in ids, "untagged chunks are kept (mirrors matches_filters)"
        assert "d2" not in ids, "off-domain chunk must be filtered natively"
    finally:
        store._client.drop_collection(settings.milvus_collection)
