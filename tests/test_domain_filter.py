"""Domain filter tests: off-domain chunks dropped, fallback preserved (spec §18)."""

from backend.core.models import EvidenceChunk
from backend.retrieval.coordinator import apply_domain_filter


def _chunk(domains):
    metadata = {"legal_domains": domains} if domains else {}
    return EvidenceChunk(content="texte", metadata=metadata)


def test_matching_and_untagged_chunks_are_kept():
    labor = _chunk(["labor_code"])
    commercial = _chunk(["commercial_law", "ohada_law"])
    untagged = _chunk([])
    filtered, applied = apply_domain_filter([labor, commercial, untagged], ["labor_code"])
    assert applied
    assert labor in filtered
    assert untagged in filtered, "untagged chunks must be kept"
    assert commercial not in filtered, "off-domain chunk must be dropped"


def test_no_query_domains_disables_the_filter():
    chunks = [_chunk(["commercial_law"]), _chunk(["labor_code"])]
    filtered, applied = apply_domain_filter(chunks, [])
    assert not applied
    assert filtered == chunks


def test_empty_match_falls_back_to_unfiltered():
    chunks = [_chunk(["commercial_law"]), _chunk(["labor_code"])]
    filtered, applied = apply_domain_filter(chunks, ["tax_law"])
    assert not applied, "a domain guess must never empty the evidence set"
    assert filtered == chunks


def test_query_domain_inference_drives_the_filter():
    from backend.ingestion.classification import infer_legal_domains

    assert "labor_code" in infer_legal_domains(
        "Quels sont les droits d'un salarié licencié ?"
    )
    assert "commercial_law" in infer_legal_domains(
        "création d'une SARL en droit OHADA"
    )
