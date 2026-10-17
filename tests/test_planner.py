"""Heuristic planner tests (offline)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from backend.core.config import Settings
from backend.core.models import QuestionType, SearchKind
from backend.planner.agent import PlannerAgent, heuristic_plan


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


def test_heuristic_plan_decomposes_broad_rights_question():
    """Broad rights questions get a deterministic issue-based decomposition."""
    query = "Quels sont les droits d'un salarié licencié au Burkina Faso ?"
    plan = heuristic_plan(query)
    assert plan.question_type == QuestionType.RIGHTS
    assert plan.temporal_intent == "any"
    assert plan.legal_domains == ["labor_code"]
    assert "decomposition" in plan.rationale
    # the raw query stays the first sub-question, followed by the 7 sub-issues
    assert plan.sub_questions[0] == query
    assert len(plan.sub_questions) == 8
    assert "préavis" in " ".join(plan.sub_questions)
    # one keyword task per sub-issue, on top of the raw-query tasks
    keyword_queries = [t.query for t in plan.tasks if t.kind == SearchKind.KEYWORD]
    assert keyword_queries[0] == query
    assert any("préavis" in q for q in keyword_queries[1:])
    assert any("juridiction compétente" in q for q in keyword_queries[1:])


def test_heuristic_plan_sets_question_type_and_temporal_intent():
    plan = heuristic_plan("Quelle était la loi en vigueur le 15/03/2020 ?")
    assert plan.question_type == QuestionType.HISTORICAL
    assert plan.temporal_intent == "historical"
    # no taxonomy match => no decomposition
    assert plan.sub_questions == ["Quelle était la loi en vigueur le 15/03/2020 ?"]


def test_specific_question_not_expanded():
    plan = heuristic_plan("Quel tribunal est compétent pour un litige de voisinage ?")
    assert plan.sub_questions == ["Quel tribunal est compétent pour un litige de voisinage ?"]


def test_tool_transcript_falls_back_to_heuristic_plan():
    query = "Quelles sont les règles OHADA applicables à la création d'une SARL ?"
    transcript = (
        "Here are the tool results:\n--- Tool results ---\n"
        "Tool build_search_tasks({\"query\": \"build a focused retrieval plan\"}): []"
    )
    result = PlannerAgent()._parse_final(
        transcript,
        {"query": query},
        SimpleNamespace(settings=Settings()),
        [],
    )
    plan = result["plan"]
    assert plan.sub_questions[0] == query
    assert all("tool results" not in question.lower() for question in plan.sub_questions)
    assert all("tool results" not in task.query.lower() for task in plan.tasks)


def test_fenced_json_plan_is_parsed():
    query = "Quelles sont les règles OHADA applicables à la création d'une SARL ?"
    raw = '''```json
{
  "sub_questions": ["capital social", "statuts"],
  "tasks": [
    {"kind": "vector", "query": "société à responsabilité limitée", "top_k": 8, "filters": {}},
    {"kind": "keyword", "query": "capital social doit être d'un million de francs CFA au moins", "top_k": 8, "filters": {}}
  ],
  "legal_domains": ["ohada_law"],
  "retrieval_language": "fr",
  "response_language": "fr",
  "scenario_date": null,
  "rationale": "plan de test"
}
```'''
    result = PlannerAgent()._parse_final(
        raw,
        {"query": query},
        SimpleNamespace(settings=Settings()),
        [],
    )
    plan = result["plan"]
    assert plan.sub_questions[:2] == ["capital social", "statuts"]
    assert "capital social doit être d'un million de francs CFA au moins" in plan.sub_questions
    assert "société à responsabilité limitée constitution capital social parts sociales" in plan.sub_questions
    assert "immatriculation RCCM publicité Journal Officiel" in plan.sub_questions
    queries = {task.query for task in plan.tasks}
    assert "capital social doit être d'un million de francs CFA au moins" in queries
    assert "société à responsabilité limitée constitution capital social parts sociales" in queries
    assert "immatriculation RCCM publicité Journal Officiel" in queries
    assert any(task.query == "capital social doit être d'un million de francs CFA au moins" and task.kind == SearchKind.VECTOR for task in plan.tasks)
