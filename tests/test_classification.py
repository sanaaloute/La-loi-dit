"""Classification tests: document_type / law_number inference (spec §6, §9)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.core.models import AuthorityLevel, DocumentType
from backend.ingestion.classification import (
    extract_law_number,
    infer_document_type,
)
from backend.ingestion.loaders import ExtractedDocument
from backend.ingestion.pipeline import IngestionPipeline


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Code du travail", DocumentType.CODE),
        ("Code pénal du Burkina Faso", DocumentType.CODE),
        ("Loi n° 028-2008/AN portant code du travail", DocumentType.CODE),  # "code" wins
        ("Loi n° 010-2017/AN", DocumentType.LAW),
        ("Décret n° 2020-0321", DocumentType.DECREE),
        ("Ordonnance n° 91-0045", DocumentType.ORDINANCE),
        ("Arrêté n° 2015-118", DocumentType.DECISION),
        ("Décision n° 2021-007 du Conseil", DocumentType.DECISION),
        ("Arrêt de la Cour suprême", DocumentType.CASE_LAW),
        ("Recueil de jurisprudence", DocumentType.CASE_LAW),
        ("Traité OHADA", DocumentType.TREATY),
        ("Acte uniforme relatif aux sûretés", DocumentType.TREATY),
        ("Note de service interne", None),
    ],
)
def test_infer_document_type(name, expected):
    assert infer_document_type(name) == expected


def test_infer_document_type_falls_back_to_text_sample():
    sample = "Vu la loi n° 028-2008/AN; le présent décret est pris..."
    assert infer_document_type("texte-reglementaire-2020.txt", sample) == DocumentType.DECREE


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Code du travail du Burkina Faso (Loi 028-2008/AN)", "028-2008/AN"),
        ("loi n° 010-2017/AN", "010-2017/AN"),
        ("Loi N°010-2017/AN", "010-2017/AN"),
        ("Décret n° 2020-0321 du 15 avril", "2020-0321"),
        ("Ordonnance 91-0045/PRES", "91-0045/PRES"),
        ("Arrêté conjoint n° 2015-118/MEF", "2015-118/MEF"),
        ("Loi de finances", None),  # no digits: not a law number
        ("Constitution du Burkina Faso", None),
    ],
)
def test_extract_law_number(name, expected):
    assert extract_law_number(name) == expected


def _doc(name: str, text: str = "Code du travail. Article 1. Dispositions.") -> ExtractedDocument:
    return ExtractedDocument(name=name, text=text, pages=[text], metadata={})


def test_enrich_metadata_fills_structured_fields_from_title_map():
    enriched = IngestionPipeline._enrich_metadata({}, _doc("code-du-travail-burkina-faso.pdf"))
    assert enriched["document_name"] == "Code du travail du Burkina Faso (Loi 028-2008/AN)"
    assert enriched["law_number"] == "028-2008/AN"
    assert enriched["document_type"] == DocumentType.CODE
    assert enriched["authority"] == AuthorityLevel.LAW


def test_enrich_metadata_keeps_explicit_values():
    enriched = IngestionPipeline._enrich_metadata(
        {"document_type": DocumentType.TREATY, "law_number": "X-1"},
        _doc("code-du-travail-burkina-faso.pdf"),
    )
    assert enriched["document_type"] == DocumentType.TREATY
    assert enriched["law_number"] == "X-1"


def test_enrich_metadata_sets_issuing_authority_from_government_body():
    enriched = IngestionPipeline._enrich_metadata(
        {"government_body": "Ministère du Travail"}, _doc("decret-test.pdf")
    )
    assert enriched["issuing_authority"] == "Ministère du Travail"


def test_enrich_metadata_defaults_validity_from_effective_date():
    future = date.today() + timedelta(days=30)
    past = date.today() - timedelta(days=30)

    enriched = IngestionPipeline._enrich_metadata(
        {"effective_date": future.isoformat()}, _doc("loi-future.pdf")
    )
    assert enriched["valid_from"] == future
    assert enriched["status"] == "future"

    enriched = IngestionPipeline._enrich_metadata(
        {"effective_date": past.isoformat()}, _doc("loi-passee.pdf")
    )
    assert enriched["valid_from"] == past
    assert "status" not in enriched  # model default ("active") stands


def test_stamp_document_metadata_propagates_to_chunks():
    from backend.core.models import EvidenceChunk

    chunks = [EvidenceChunk(content="a"), EvidenceChunk(content="b")]
    IngestionPipeline._stamp_document_metadata(
        chunks,
        {
            "document_type": "law",
            "law_number": "028-2008/AN",
            "issuing_authority": "Assemblée nationale",
            "status": "active",
            "valid_from": "2008-05-13",
        },
    )
    for chunk in chunks:
        assert chunk.document_type == DocumentType.LAW
        assert chunk.law_number == "028-2008/AN"
        assert chunk.issuing_authority == "Assemblée nationale"
        assert chunk.valid_from == date(2008, 5, 13)
        assert chunk.jurisdiction == "Burkina Faso"  # untouched default


def test_stamp_document_metadata_ignores_unresolved_fields():
    from backend.core.models import EvidenceChunk

    chunk = EvidenceChunk(content="a")
    IngestionPipeline._stamp_document_metadata([chunk], {"document_type": "not-a-type"})
    assert chunk.document_type is None
    assert chunk.law_number is None
