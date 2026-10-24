"""Ingestion-time legal_domains tagging.

Regression tests for the tag pollution found on the live index: folder slugs
(bf/ohada/cima) leaked in as pseudo-domains and 2000-char text-sample
inference tagged classic codes with 5+ domains, making the retrieval-time
domain filter useless.
"""

from backend.ingestion.loaders import ExtractedDocument
from backend.ingestion.pipeline import IngestionPipeline


def _enrich(metadata, name="burkina-faso_code_travail_013-2026-alp_code-du-travail.pdf", text=""):
    return IngestionPipeline._enrich_metadata(metadata, ExtractedDocument(name=name, text=text))


def test_folder_jurisdiction_slugs_are_dropped():
    metadata = _enrich({"folder_domains": ["bf"]})
    assert "bf" not in metadata["legal_domains"]
    assert metadata["legal_domains"] == ["labor_code"]


def test_text_sample_no_longer_dilutes_domains():
    # A labour code mentioning property, tax and commerce must stay labor-only.
    noisy_text = "propriété foncière impôts commerce sociétés civiles " * 100
    metadata = _enrich({"folder_domains": ["bf"]}, text=noisy_text)
    assert metadata["legal_domains"] == ["labor_code"]


def test_curated_domains_win_and_are_validated():
    metadata = _enrich({"legal_domains": ["civil_law", "bf"], "folder_domains": ["ohada"]})
    assert metadata["legal_domains"] == ["civil_law"]


def test_unknown_document_gets_validated_inference():
    metadata = _enrich({"folder_domains": ["cima"]}, name="ohada_acte-uniforme_suretes_2010.pdf")
    assert set(metadata["legal_domains"]) <= {"ohada_law", "commercial_law"}
    assert "cima" not in metadata["legal_domains"]
