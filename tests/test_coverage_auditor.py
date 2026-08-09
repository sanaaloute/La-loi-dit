"""Tests for the deterministic coverage auditor (spec §22)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agents.coverage_auditor import CoverageAuditorAgent, audit_coverage
from backend.core.models import EvidenceChunk, RetrievalPlan

PREAVIS_TEXT = (
    "La durée du préavis de licenciement pour l'employé mensualisé est fixée "
    "à un mois, renouvelable selon la convention collective applicable."
)


def _chunk(content: str = PREAVIS_TEXT) -> EvidenceChunk:
    return EvidenceChunk(document_name="Code du travail", content=content)


def test_full_coverage_no_retry():
    report = audit_coverage(
        [
            "durée du préavis de licenciement",
            "préavis pour un employé mensualisé",
        ],
        [_chunk()],
        query="préavis licenciement",
    )
    assert report.coverage == 1.0
    assert not report.missing_issues
    assert not report.needs_more_retrieval


def test_partial_coverage_flags_missing_and_retry():
    missing_q = "partage des biens après divorce"
    report = audit_coverage(
        ["préavis de licenciement", missing_q],
        [_chunk()],  # only covers the préavis sub-question
        query="préavis licenciement",
    )
    assert report.coverage == 0.5
    assert report.missing_issues == [missing_q]
    assert report.needs_more_retrieval


def test_no_evidence_everything_missing():
    questions = ["préavis de licenciement", "partage des biens"]
    report = audit_coverage(questions, [], query="préavis licenciement")
    assert report.coverage == 0.0
    assert report.missing_issues == questions
    assert report.needs_more_retrieval


def test_empty_sub_questions_falls_back_to_query():
    report = audit_coverage([], [_chunk()], query="préavis licenciement")
    assert report.coverage == 1.0
    assert not report.needs_more_retrieval


def test_nothing_to_audit_is_not_a_gap():
    report = audit_coverage([], [_chunk()], query="")
    assert report.coverage == 1.0
    assert not report.missing_issues
    assert not report.needs_more_retrieval


def test_threshold_is_configurable():
    questions = ["préavis de licenciement", "partage des biens après divorce"]
    report = audit_coverage(questions, [_chunk()], query="préavis", threshold=0.4)
    assert report.coverage == 0.5
    assert not report.needs_more_retrieval  # 0.5 >= 0.4: no retry


def test_answer_text_counts_as_additional_text():
    report = audit_coverage(
        ["partage des biens après divorce"],
        [],
        query="divorce",
        answer_text="Le partage des biens est prononcé par le juge du divorce.",
    )
    assert report.coverage == 1.0


@pytest.mark.asyncio
async def test_node_sets_retry_flag_within_budget(settings):
    agent = CoverageAuditorAgent()
    state = {
        "query": "préavis licenciement",
        "plan": RetrievalPlan(sub_questions=["préavis de licenciement", "partage des biens après divorce"]),
        "ranked_evidence": [_chunk()],
        "retrieval_retries": 0,
        "trace": [],
    }
    result = await agent.run(state, SimpleNamespace(settings=settings))
    report = result["coverage_report"]
    assert report.needs_more_retrieval
    assert result["needs_more_retrieval"] is True
    assert any(t.startswith("coverage_auditor") for t in result["trace"])


@pytest.mark.asyncio
async def test_node_no_retry_when_budget_exhausted(settings):
    agent = CoverageAuditorAgent()
    state = {
        "query": "préavis licenciement",
        "plan": RetrievalPlan(sub_questions=["partage des biens après divorce"]),
        "ranked_evidence": [_chunk()],
        "retrieval_retries": settings.max_retrieval_retries,
        "trace": [],
    }
    result = await agent.run(state, SimpleNamespace(settings=settings))
    assert result["coverage_report"].needs_more_retrieval  # gap still reported
    assert result["needs_more_retrieval"] is False  # but no retry requested
