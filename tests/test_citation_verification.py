"""Citation verification tests (pure logic, offline)."""

from __future__ import annotations

from backend.agents.citation_verification import citation_verification_node, extract_citations
from backend.core.models import EvidenceChunk


def _evidence() -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            document_name="Code du travail",
            article="95",
            content="Le préavis est d'un mois pour les employés.",
        ),
        EvidenceChunk(
            document_name="Code général des impôts",
            article="271",
            content="Le taux de la TVA est de 18%.",
        ),
    ]


def test_fabricated_citation_rejected_valid_verified():
    evidence = _evidence()
    draft = "Le préavis est d'un mois [1]. La TVA est de 18% [2]. Le délai est de 99 jours [9]."
    verified, rejected = extract_citations(draft, evidence)

    assert [c.label for c in verified] == ["[1]", "[2]"]
    assert all(c.verified for c in verified)
    assert verified[0].chunk_id == evidence[0].chunk_id
    assert verified[0].document_name == "Code du travail"

    assert [c.label for c in rejected] == ["[9]"]
    assert not rejected[0].verified
    assert rejected[0].chunk_id is None


async def test_node_strips_fabricated_citations_and_scores_accuracy():
    evidence = _evidence()[:1]
    state = {
        "query": "préavis ?",
        "draft_answer": "Le préavis est d'un mois [1] et de 99 ans [9].",
        "ranked_evidence": evidence,
        "trace": [],
        "errors": [],
    }
    update = await citation_verification_node(state, ctx=None)
    assert "[9]" not in update["draft_answer"]
    assert "[1]" in update["draft_answer"]
    assert update["citation_accuracy"] == 0.5
    assert any("rejetée" in e for e in update["errors"])
