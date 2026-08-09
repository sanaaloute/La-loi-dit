"""Heuristic planner tests (offline)."""

from __future__ import annotations

from datetime import date

from backend.core.models import SearchKind
from backend.planner.agent import heuristic_plan


def test_government_task_added_for_decret_query():
    plan = heuristic_plan("Quel décret porte application du Code du travail ?")
    assert any(t.kind == SearchKind.GOVERNMENT for t in plan.tasks)
    # vector + keyword are always planned
    assert any(t.kind == SearchKind.VECTOR for t in plan.tasks)
    assert any(t.kind == SearchKind.KEYWORD for t in plan.tasks)


def test_french_language_detected():
    plan = heuristic_plan("Quelle est la durée du préavis de licenciement ?")
    assert plan.response_language == "fr"
    assert plan.retrieval_language == "fr"


def test_scenario_date_extracted():
    plan = heuristic_plan("Quelle loi était en vigueur le 15/03/2020 ?")
    assert plan.scenario_date == date(2020, 3, 15)

    plan_iso = heuristic_plan("Situation juridique au 2021-11-30 svp.")
    assert plan_iso.scenario_date == date(2021, 11, 30)


def test_heuristic_plan_does_not_decompose():
    """Decomposition is LLM-only: the heuristic fallback plans direct searches."""
    plan = heuristic_plan("Quels sont les droits d'un salarié licencié au Burkina Faso ?")
    assert plan.sub_questions == ["Quels sont les droits d'un salarié licencié au Burkina Faso ?"]
    queries = [t.query for t in plan.tasks]
    assert all("préavis" not in q or q == plan.sub_questions[0] for q in queries)
    assert plan.legal_domains == ["labor_code"]


def test_specific_question_not_expanded():
    plan = heuristic_plan("Quel tribunal est compétent pour un litige de voisinage ?")
    assert plan.sub_questions == ["Quel tribunal est compétent pour un litige de voisinage ?"]
