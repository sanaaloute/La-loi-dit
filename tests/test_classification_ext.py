"""Classification extensibility tests: a new legal document type dropped into
``data/legal_docs/`` must work with data files only, never code changes.

Covers the external domain taxonomy (data/legal_domains.json), folder-derived
domains, the metadata manifest/sidecar defaults and the LLM classification
fallback.
"""

from __future__ import annotations

import json

import pytest

import backend.ingestion.classification as classification
from backend.core.models import AuthorityLevel, DocumentType
from backend.ingestion.classification import (
    _DOMAIN_KEYWORDS,
    domain_slug,
    infer_legal_domains,
    load_domain_keywords,
)
from backend.ingestion.loaders import ExtractedDocument
from backend.ingestion.pipeline import IngestionPipeline

#: Neutral name/text that match no heuristic keyword (authority, domain, type).
_NEUTRAL_NAME = "note-horaires-xyz.txt"
_NEUTRAL_TEXT = (
    "Note d information relative aux horaires d ouverture des bureaux "
    "pendant la periode estivale."
)


class _FakeLLM:
    """Minimal async LLM stand-in with a non-mock provider."""

    provider = "openai"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, **kwargs) -> str:
        self.calls.append((system, user))
        return self.response


# ---------------------------------------------------------------------------
# Feature 1 — external domain taxonomy
# ---------------------------------------------------------------------------


def test_load_domain_keywords_from_tmp_file(tmp_path):
    taxonomy = tmp_path / "legal_domains.json"
    taxonomy.write_text(
        json.dumps({"version": 1, "domains": {"space_law": ["satellite", "orbite"]}}),
        encoding="utf-8",
    )
    assert load_domain_keywords(taxonomy) == {"space_law": ["satellite", "orbite"]}


def test_load_domain_keywords_corrupt_falls_back_to_builtin(tmp_path):
    corrupt = tmp_path / "legal_domains.json"
    corrupt.write_text("{not json", encoding="utf-8")
    loaded = load_domain_keywords(corrupt)
    assert loaded == {d: list(kws) for d, kws in _DOMAIN_KEYWORDS.items()}


def test_load_domain_keywords_missing_falls_back_to_builtin(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    loaded = load_domain_keywords(missing)
    assert loaded == {d: list(kws) for d, kws in _DOMAIN_KEYWORDS.items()}


def test_infer_legal_domains_recognizes_brand_new_domain(monkeypatch, tmp_path):
    """A domain that exists only in the taxonomy file is inferred — no code change."""
    taxonomy = tmp_path / "legal_domains.json"
    taxonomy.write_text(
        json.dumps(
            {
                "version": 1,
                "domains": {
                    "labor_code": ["travail", "licenci"],
                    "traffic_law": ["code de la route", "radar", "amende"],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(classification, "_DEFAULT_DOMAINS_PATH", taxonomy)
    try:
        assert infer_legal_domains("Contestation d une amende de radar") == ["traffic_law"]
        assert infer_legal_domains("contrat de travail") == ["labor_code"]
    finally:
        classification._DOMAINS_CACHE.pop(f"{taxonomy}#domain_keywords", None)


# ---------------------------------------------------------------------------
# Feature 2 — folder = domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Code de la route", "code_de_la_route"),
        ("Santé & Sécurité!", "sante_securite"),
        ("marchés_publics", "marches_publics"),
        ("  --Espèces--  ", "especes"),
    ],
)
def test_domain_slug(name, expected):
    assert domain_slug(name) == expected


def test_enrich_metadata_unions_explicit_folder_and_inferred_domains():
    text = "Le salarié licencié a droit à un préavis."
    doc = ExtractedDocument(name="doc.txt", text=text, pages=[text], metadata={})
    enriched = IngestionPipeline._enrich_metadata(
        {"legal_domains": ["custom_domain"], "folder_domains": ["traffic_law", "labor_code"]},
        doc,
    )
    # explicit + folder + inferred ("salari"/"licenci"/"preavis" -> labor_code),
    # deduped, order preserved; the pass-through key is consumed.
    assert enriched["legal_domains"] == ["custom_domain", "traffic_law", "labor_code"]
    assert "folder_domains" not in enriched


def test_enrich_metadata_empty_union_stays_empty():
    doc = ExtractedDocument(name=_NEUTRAL_NAME, text=_NEUTRAL_TEXT, pages=[_NEUTRAL_TEXT], metadata={})
    enriched = IngestionPipeline._enrich_metadata({}, doc)
    assert enriched["legal_domains"] == []


async def test_reindex_subdirectory_becomes_domain(ctx, tmp_path):
    sub = tmp_path / "Code de la route"
    sub.mkdir()
    (sub / "reglement-stationnement.txt").write_text(_NEUTRAL_TEXT, encoding="utf-8")
    results = await IngestionPipeline(ctx).reindex_directory(tmp_path)
    assert results[0].status == "indexed"
    chunks = await ctx.vector_store.get_by_document_id(results[0].document_id)
    assert chunks
    for chunk in chunks:
        assert chunk.metadata["legal_domains"] == ["code_de_la_route"]


# ---------------------------------------------------------------------------
# Feature 3 — metadata manifest + sidecar
# ---------------------------------------------------------------------------


async def test_manifest_and_sidecar_defaults_precedence(ctx, monkeypatch, tmp_path):
    """Sidecar wins over the manifest; both win over keyword inference."""
    sources = tmp_path / "legal_sources.json"
    sources.write_text(
        json.dumps(
            {
                "version": 1,
                "document_metadata": {
                    _NEUTRAL_NAME: {
                        "authority": "decree",
                        "document_type": "decree",
                        "legal_domains": ["traffic_law"],
                        "law_number": "2024-0001",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    from backend.core.config import Settings

    monkeypatch.setattr(
        "backend.core.config.get_settings",
        lambda: Settings(legal_sources_path=str(sources)),
    )
    doc_path = tmp_path / _NEUTRAL_NAME
    doc_path.write_text(_NEUTRAL_TEXT, encoding="utf-8")
    sidecar = tmp_path / (_NEUTRAL_NAME + ".meta.json")
    sidecar.write_text(
        json.dumps({"authority": "order", "legal_domains": ["municipal_regulations"]}),
        encoding="utf-8",
    )

    result = await IngestionPipeline(ctx).ingest_path(doc_path)
    assert result.status == "indexed"
    chunks = await ctx.vector_store.get_by_document_id(result.document_id)
    assert chunks
    for chunk in chunks:
        assert chunk.authority == AuthorityLevel.ORDER  # sidecar beats manifest
        assert chunk.document_type == DocumentType.DECREE  # manifest value kept
        assert chunk.law_number == "2024-0001"  # manifest value kept
        # sidecar legal_domains replace the manifest's and join the union
        assert chunk.metadata["legal_domains"] == ["municipal_regulations"]


# ---------------------------------------------------------------------------
# Feature 4 — LLM classification fallback
# ---------------------------------------------------------------------------


async def test_llm_fallback_fills_fields_when_heuristics_find_nothing(ctx):
    response = (
        "```json\n"
        '{"document_title": "Note d information", "authority": "law", '
        '"document_type": "law", "legal_domains": ["traffic_law", "not_a_domain"]}\n'
        "```"
    )
    ctx.llm = _FakeLLM(response)
    result = await IngestionPipeline(ctx).ingest_text(_NEUTRAL_NAME, _NEUTRAL_TEXT)
    assert result.status == "indexed"
    assert len(ctx.llm.calls) == 1  # exactly one completion
    chunks = await ctx.vector_store.get_by_document_id(result.document_id)
    assert chunks
    for chunk in chunks:
        assert chunk.authority == AuthorityLevel.LAW
        assert chunk.document_type == DocumentType.LAW
        # filtered to the loaded taxonomy keys ("not_a_domain" dropped)
        assert chunk.metadata["legal_domains"] == ["traffic_law"]


async def test_llm_fallback_invalid_json_keeps_heuristics(ctx):
    ctx.llm = _FakeLLM("this is not json at all")
    result = await IngestionPipeline(ctx).ingest_text(_NEUTRAL_NAME, _NEUTRAL_TEXT)
    assert result.status == "indexed"  # never fails ingestion
    assert len(ctx.llm.calls) == 1
    chunks = await ctx.vector_store.get_by_document_id(result.document_id)
    assert chunks
    for chunk in chunks:
        assert chunk.authority == AuthorityLevel.UNKNOWN
        assert chunk.document_type is None
        assert chunk.metadata["legal_domains"] == []


async def test_llm_fallback_disabled_flag_never_calls_llm(ctx):
    ctx.settings.ingestion_llm_classification_enabled = False
    ctx.llm = _FakeLLM('{"authority": "law", "legal_domains": ["traffic_law"]}')
    result = await IngestionPipeline(ctx).ingest_text(_NEUTRAL_NAME, _NEUTRAL_TEXT)
    assert result.status == "indexed"
    assert ctx.llm.calls == []
    chunks = await ctx.vector_store.get_by_document_id(result.document_id)
    assert chunks
    for chunk in chunks:
        assert chunk.authority == AuthorityLevel.UNKNOWN
        assert chunk.metadata["legal_domains"] == []


async def test_llm_fallback_skipped_when_heuristics_classify(ctx):
    """Known documents never pay for the fallback (happy path unchanged)."""
    ctx.llm = _FakeLLM('{"authority": "law", "legal_domains": ["traffic_law"]}')
    result = await IngestionPipeline(ctx).ingest_text(
        "code-travail-llm.txt",
        "Code du travail. Article 1: tout salarié a droit à un salaire.",
    )
    assert result.status == "indexed"
    assert ctx.llm.calls == []
