"""Deterministic coverage auditor (spec §22).

Checks — without any LLM call — whether every planned sub-question is backed
by at least one retrieved evidence chunk, using discriminative-term matching.
Runs after evidence ranking and BEFORE drafting, so a coverage gap triggers
one bounded re-retrieval pass (fanned out on the missing sub-questions)
instead of being discovered after the answer is written.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.agents.agent import Agent
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.models import CoverageReport, EvidenceChunk
from backend.core.state import GraphState

# Function words ignored when matching a sub-question against evidence content.
_COVERAGE_STOPWORDS = {
    "quels", "quelles", "quel", "quelle", "sont", "est", "être", "avoir",
    "dans", "pour", "avec", "sans", "sous", "entre", "leur", "leurs", "selon",
    "code", "droits", "droit", "burkina", "faso", "comment", "quoi", "quand",
    "après", "avant", "contre", "chez", "tout", "tous", "toute", "toutes",
    "faire", "fait", "doit", "peut", "plus", "très", "aussi", "ainsi",
}


def discriminative_terms(text: str, settings: Optional[Any] = None) -> list[str]:
    """Significant terms of a text: lowercase alpha tokens (length >=
    ``settings.coverage_term_min_length``) minus stopwords."""
    settings = settings or get_settings()
    min_length = settings.coverage_term_min_length
    return [
        t
        for t in re.findall(rf"[a-zàâäçéèêëîïôöùûü]{{{min_length},}}", text.lower())
        if t not in _COVERAGE_STOPWORDS
    ]


def question_is_covered(
    question: str,
    texts: list[str],
    query_terms: set[str],
    settings: Optional[Any] = None,
) -> bool:
    """True when one text carries at least ``settings.coverage_term_match_ratio``
    of the question's discriminative terms.

    Discriminative terms are those not already in the main query — "partage",
    "biens", not "divorce". When the question adds nothing beyond the query,
    its own terms are used; a term-less question counts as covered.
    """
    settings = settings or get_settings()
    terms = [t for t in discriminative_terms(question, settings) if t not in query_terms]
    if not terms:
        terms = discriminative_terms(question, settings)
    if not terms:
        return True
    threshold = max(1, int(len(terms) * settings.coverage_term_match_ratio))
    return any(sum(1 for t in terms if t in content) >= threshold for content in texts)


def audit_coverage(
    sub_questions: list[str],
    evidence: list[EvidenceChunk],
    *,
    query: str = "",
    answer_text: Optional[str] = None,
    threshold: float = 0.6,
    settings: Optional[Any] = None,
) -> CoverageReport:
    """Audit how many sub-questions are backed by evidence (pure function).

    ``answer_text``, when given, is matched as an additional text so a drafted
    answer can be audited the same way. ``needs_more_retrieval`` is true when
    coverage falls below ``threshold`` AND at least one issue is missing. With
    nothing to audit (no sub-questions and no query) there is no gap: the
    report is full coverage with no retry.  ``settings`` defaults to the
    process-wide ``get_settings()``.
    """
    settings = settings or get_settings()
    questions = [q for q in sub_questions if q.strip()]
    if not questions and query.strip():
        questions = [query]
    if not questions:
        return CoverageReport(coverage=1.0)

    texts = [c.content.lower() for c in evidence]
    if answer_text:
        texts.append(answer_text.lower())

    covered: list[str] = []
    missing: list[str] = []
    if texts:
        query_terms = set(discriminative_terms(query, settings))
        for question in questions:
            target = covered if question_is_covered(question, texts, query_terms, settings) else missing
            target.append(question)
    else:
        missing = list(questions)

    coverage = len(covered) / len(questions)
    return CoverageReport(
        coverage=coverage,
        covered_issues=covered,
        missing_issues=missing,
        needs_more_retrieval=coverage < threshold and bool(missing),
    )


class CoverageAuditorAgent(Agent):
    """Deterministic pre-drafting audit: flags sub-questions lacking evidence."""

    name = "coverage_auditor"
    system_prompt = (
        "You are the coverage auditor. Deterministically check whether every "
        "planned sub-question is backed by retrieved evidence and flag gaps "
        "for one bounded re-retrieval before drafting."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        plan = state.get("plan")
        sub_questions = [q for q in (plan.sub_questions if plan else []) if q.strip()]
        if not sub_questions:
            sub_questions = [state.get("query", "")]
        report = audit_coverage(
            sub_questions,
            list(state.get("ranked_evidence", [])),
            query=state.get("query", ""),
            threshold=ctx.settings.coverage_retry_threshold,
            settings=ctx.settings,
        )
        can_retry = (
            report.needs_more_retrieval
            and state.get("retrieval_retries", 0) < ctx.settings.max_retrieval_retries
        )
        missing = len(report.missing_issues)
        return {
            "coverage_report": report,
            # Same key the reasoning/reflection retry paths use: the graph
            # routes on it and retrieval_merge counts the pass and resets it.
            "needs_more_retrieval": can_retry,
            "trace": [
                *state.get("trace", []),
                f"coverage_auditor: coverage {report.coverage:.0%} ({missing} missing)"
                + (", re-retrieval requested" if can_retry else ""),
            ],
        }


coverage_auditor_node = CoverageAuditorAgent().run
