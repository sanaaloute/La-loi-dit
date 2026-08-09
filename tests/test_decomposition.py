"""Deterministic decomposition tests (offline, spec §15)."""

from __future__ import annotations

from backend.core.models import QuestionType
from backend.planner.decomposition import deterministic_decompose

GOLDEN_QUERY = "Quels sont les droits d'un salarié licencié au Burkina Faso ?"


def test_licenciement_decomposes_into_seven_issue_categories():
    """The golden broad rights query decomposes into the 7 expected issues."""
    issues = deterministic_decompose(GOLDEN_QUERY, QuestionType.RIGHTS, ["labor_code"])
    assert len(issues) == 7
    joined = " | ".join(issues)
    for expected in (
        "motifs",
        "préavis",
        "indemnité de licenciement",
        "licenciement abusif",
        "droits acquis",
        "voies de recours",
        "juridiction compétente",
    ):
        assert expected in joined


def test_decomposition_works_without_domains():
    """With no classified domains, keyword matches alone trigger the taxonomy."""
    issues = deterministic_decompose(GOLDEN_QUERY, QuestionType.RIGHTS, [])
    assert len(issues) == 7


def test_unrelated_query_returns_empty():
    assert deterministic_decompose("Quel est le prix du timbre fiscal ?", QuestionType.FACTUAL, ["tax_law"]) == []
    assert deterministic_decompose("Parle-moi de la météo à Ouagadougou.", QuestionType.GENERAL, []) == []


def test_specific_question_type_not_decomposed():
    """Specific (non-broad) question types are never decomposed, even when a
    taxonomy keyword matches."""
    assert (
        deterministic_decompose(
            "Quelle est la durée du préavis de licenciement ?",
            QuestionType.FACTUAL,
            ["labor_code"],
        )
        == []
    )
    assert (
        deterministic_decompose(
            "Comment calculer l'indemnité de licenciement ?",
            QuestionType.CALCULATION,
            ["labor_code"],
        )
        == []
    )


def test_domain_filter_skips_unrelated_topics():
    """A topic keyword matching outside the classified domains is skipped."""
    assert (
        deterministic_decompose(
            "Quels sont les droits d'un salarié licencié ?",
            QuestionType.RIGHTS,
            ["tax_law"],
        )
        == []
    )


def test_other_taxonomy_topics():
    divorce = deterministic_decompose(
        "Quelle est la procédure de divorce au Burkina Faso ?",
        QuestionType.PROCEDURE,
        ["family_code"],
    )
    assert divorce
    assert any("divorce" in issue for issue in divorce)

    succession = deterministic_decompose(
        "Quels sont les droits des héritiers en cas de succession ?",
        QuestionType.RIGHTS,
        ["family_code"],
    )
    assert succession
    assert any("partage" in issue or "succession" in issue for issue in succession)
