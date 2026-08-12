"""Citation verification tests (pure logic, offline)."""

from __future__ import annotations

from backend.agents.citation_verification import (
    citation_verification_node,
    extract_citations,
    renumber_citations,
)
from backend.core.models import Citation, EvidenceChunk, FinalAnswer


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


# ---------------------------------------------------------------------------
# User-facing renumbering: markers always start at [1] in appearance order
# ---------------------------------------------------------------------------


def test_renumber_starts_at_one_in_appearance_order():
    citations = [
        Citation(label="[3]", document_name="Charte de la Transition", article="168"),
        Citation(label="[7]", document_name="Code du travail", article="95"),
    ]
    text = "Selon la Charte [3], puis le Code [7] et encore la Charte [3]."
    new_text, relabeled = renumber_citations(text, citations)

    assert new_text == "Selon la Charte [1], puis le Code [2] et encore la Charte [1]."
    assert [c.label for c in relabeled] == ["[1]", "[2]"]
    # Chunk metadata follows the relabeling.
    assert relabeled[0].document_name == "Charte de la Transition"
    assert relabeled[0].article == "168"
    assert relabeled[1].document_name == "Code du travail"


def test_renumber_swap_does_not_clobber_existing_marker():
    # [2] appears before [1]: [2] -> [1] must not rewrite the original [1].
    citations = [
        Citation(label="[1]", document_name="Doc A"),
        Citation(label="[2]", document_name="Doc B"),
    ]
    new_text, relabeled = renumber_citations("D'abord [2], ensuite [1].", citations)
    assert new_text == "D'abord [1], ensuite [2]."
    assert relabeled[0].document_name == "Doc B"
    assert relabeled[1].document_name == "Doc A"


def test_renumber_already_sequential_is_noop():
    citations = [Citation(label="[1]", document_name="Doc A")]
    text, relabeled = renumber_citations("Réponse [1].", citations)
    assert text == "Réponse [1]."
    assert [c.label for c in relabeled] == ["[1]"]


async def test_node_renumbers_final_answer_and_citations():
    final = FinalAnswer(
        answer="Selon la Charte [2].",
        citations=[Citation(label="[2]", document_name="Charte", verified=True)],
        evidence=_evidence(),
        confidence=0.9,
        language="fr",
    )
    state = {
        "query": "charte ?",
        "draft_answer": "Selon la Charte [2].",
        "ranked_evidence": _evidence(),
        "final_answer": final,
        "trace": [],
        "errors": [],
    }
    update = await citation_verification_node(state, ctx=None)
    assert update["final_answer"].answer == "Selon la Charte [1]."
    assert [c.label for c in update["final_answer"].citations] == ["[1]"]
    assert update["final_answer"].citations[0].document_name == "Charte"
