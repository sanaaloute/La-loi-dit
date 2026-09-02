"""Tests for the multi-dimensional confidence breakdown (spec §39)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agents.citation_verification import citation_verification_node
from backend.agents.response_generator import ResponseGeneratorAgent
from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.models import (
    AuthorityLevel,
    ConflictReport,
    ConfidenceBreakdown,
    EvidenceChunk,
    FinalAnswer,
    RetrievalPlan,
)


class StubLLM:
    """Scripted LLM: returns queued outputs, then empty strings."""

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)

    async def complete(self, system: str, user: str, temperature=None) -> str:
        return self.outputs.pop(0) if self.outputs else ""


ARTICLE = (
    "Article 542 — Le divorce peut être prononcé pour rupture de la vie commune, "
    "pour atteinte grave aux devoirs du mariage ou d'un commun accord des époux."
)


def _evidence() -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            document_name="Code des personnes et de la famille",
            article="542",
            content=ARTICLE,
            authority=AuthorityLevel.LAW,
            rerank_score=0.8,
        )
    ]


def _state(evidence: list[EvidenceChunk], **extra) -> dict:
    state = {
        "query": "Quelles sont les causes du divorce selon le Code des personnes et de la famille ?",
        "ranked_evidence": evidence,
        "language": "fr",
        "plan": RetrievalPlan(sub_questions=["causes du divorce"]),
        "trace": [],
        "errors": [],
        "conflicts": [],
    }
    state.update(extra)
    return state


def _ctx(settings, llm: StubLLM):
    return SimpleNamespace(llm=llm, settings=settings)


@pytest.mark.asyncio
async def test_breakdown_dimensions_present(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Le divorce peut être prononcé pour trois causes [1]."])
    result = await agent.run(_state(_evidence()), _ctx(settings, llm))
    breakdown = result["final_answer"].confidence_breakdown

    assert breakdown is not None
    for value in (
        breakdown.source_confidence,
        breakdown.retrieval_confidence,
        breakdown.legal_support_confidence,
        breakdown.temporal_confidence,
        breakdown.citation_confidence,
        breakdown.coverage,
    ):
        assert 0.0 <= value <= 1.0
    assert breakdown.source_confidence == AUTHORITY_WEIGHTS[AuthorityLevel.LAW]
    assert breakdown.retrieval_confidence == 0.8
    assert breakdown.coverage == 1.0  # "causes du divorce" is covered by the article
    assert breakdown.citation_confidence == 1.0  # pre-verification accuracy
    assert breakdown.temporal_confidence == 1.0  # no temporal doubt


@pytest.mark.asyncio
async def test_aggregate_is_weighted_mean_of_citation_and_coverage(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse partielle [1]."])
    state = _state(
        _evidence(),
        plan=RetrievalPlan(
            sub_questions=[
                "causes du divorce",  # covered
                "partage des biens après le divorce",  # not covered
            ]
        ),
    )
    result = await agent.run(state, _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.confidence_breakdown.coverage == 0.5
    # 0.4 * citation accuracy + 0.6 * coverage, rounded to 2 decimals.
    assert answer.confidence == round(0.4 * 1.0 + 0.6 * 0.5, 2)


@pytest.mark.asyncio
async def test_unresolved_conflict_dampens_and_lowers_temporal(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    baseline = (await agent.run(_state(_evidence()), _ctx(settings, llm)))["final_answer"]

    state = _state(
        _evidence(),
        conflicts=[
            ConflictReport(
                topic="Code du travail art. 70",
                kept_chunk_id="a",
                dropped_chunk_id="b",
                reason="conflit non résolu",
                resolved=False,
            )
        ],
    )
    result = await agent.run(state, _ctx(settings, llm))
    answer = result["final_answer"]
    # One unresolved conflict: exactly one dampening multiplier (no hard cap).
    assert answer.confidence == round(
        baseline.confidence * settings.confidence_unresolved_conflict_dampening, 2
    )
    assert answer.confidence_breakdown.temporal_confidence == 0.5


@pytest.mark.asyncio
async def test_time_sensitive_plan_with_undated_sources_lowers_temporal(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(
        _evidence(),
        plan=RetrievalPlan(sub_questions=["causes du divorce"], temporal_intent="current"),
    )
    result = await agent.run(state, _ctx(settings, llm))
    assert result["final_answer"].confidence_breakdown.temporal_confidence == 0.6


@pytest.mark.asyncio
async def test_no_evidence_breakdown_is_zero(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM([])
    result = await agent.run(_state([]), _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.confidence == 0.0
    assert answer.confidence_breakdown is not None
    assert answer.confidence_breakdown.coverage == 0.0
    assert answer.confidence_breakdown.source_confidence == 0.0


@pytest.mark.asyncio
async def test_citation_verification_updates_citation_confidence(ctx):
    """Post-verification accuracy is synced into the breakdown (spec §39)."""
    chunk = EvidenceChunk(document_name="Code du travail", content="contenu")
    final = FinalAnswer(
        answer="Réponse [1] et aussi [9].",
        confidence=0.8,
        confidence_breakdown=ConfidenceBreakdown(citation_confidence=1.0),
    )
    state = {
        "query": "question",
        "draft_answer": "Réponse [1] et aussi [9].",
        "ranked_evidence": [chunk],
        "final_answer": final,
        "trace": [],
        "errors": [],
    }
    result = await citation_verification_node(state, ctx)
    updated = result["final_answer"]
    assert result["citation_accuracy"] == 0.5  # [1] verified, [9] rejected
    assert updated.confidence == 0.4  # 0.8 * 0.5, as before
    assert updated.confidence_breakdown.citation_confidence == 0.5
