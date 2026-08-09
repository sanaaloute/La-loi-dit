"""Legal terminology lexicon and query-expansion tests (offline, spec §14/§29)."""

from __future__ import annotations

from backend.agents.tools.registry import list_tools
from backend.core.config import get_settings
from backend.core.models import SearchKind
from backend.planner.agent import heuristic_plan
from backend.planner.terminology import LEXICON, expand_terms, lookup

_MAX_EXPANSION_TASKS = get_settings().planner_max_expansion_tasks


def _keyword_queries(plan) -> list[str]:
    return [t.query for t in plan.tasks if t.kind == SearchKind.KEYWORD]


def _expansion_queries(plan) -> list[str]:
    """Keyword queries added by terminology expansion (not query/sub-issues)."""
    planned = {plan.sub_questions[0], *plan.sub_questions}
    return [q for q in _keyword_queries(plan) if q not in planned]


def test_lexicon_covers_expected_domains_and_minimum_size():
    assert len(LEXICON) >= 40
    canonicals = {entry.canonical for entry in LEXICON}
    # labour, civil, family, criminal, commercial/OHADA, administrative, tax, land
    for expected in (
        "licenciement",
        "bail",
        "divorce",
        "infraction",
        "société commerciale",
        "RCCM",
        "fonction publique",
        "impôt",
        "propriété foncière",
    ):
        assert expected in canonicals


def test_lookup_is_accent_and_case_insensitive():
    assert lookup("Licenciement").canonical == "licenciement"
    assert lookup("LICENCIEMENT").canonical == "licenciement"
    assert lookup("PREAVIS").canonical == "préavis"
    assert lookup("heritage").canonical == "succession"  # synonym, accents stripped
    assert lookup("sarl").canonical == "SARL"


def test_lookup_unknown_term_returns_none():
    assert lookup("xyzzy pas un terme juridique") is None


def test_expand_terms_licenciement_includes_rupture_du_contrat():
    expansions = expand_terms("droits d'un salarié licencié")
    assert "licenciement" in expansions
    assert "rupture du contrat de travail" in expansions["licenciement"]
    assert "préavis" in expansions["licenciement"]


def test_expand_terms_excludes_broader_and_narrower_terms():
    # Broader/narrower change the legal meaning and must never be used for
    # default expansion — only synonyms + related terms.
    expansions = expand_terms("licenciement")
    terms = expansions["licenciement"]
    assert "cessation du contrat de travail" not in terms  # broader
    assert "licenciement économique" not in terms  # narrower
    assert "licenciement abusif" not in terms  # narrower


def test_expand_terms_drops_terms_already_in_query():
    expansions = expand_terms("droits d'un salarié licencié")
    assert "licencié" not in expansions["licenciement"]


def test_expand_terms_unrelated_query_returns_empty():
    assert expand_terms("quelle est la météo à Ouagadougou demain") == {}


def test_heuristic_plan_adds_expansion_keyword_tasks():
    query = "Quels sont les droits d'un salarié licencié au Burkina Faso ?"
    plan = heuristic_plan(query)
    assert "terminology expansion" in plan.rationale
    assert "licenciement" in plan.rationale
    expansion_queries = _expansion_queries(plan)
    assert expansion_queries, "expected at least one terminology-expansion keyword task"
    assert any("rupture du contrat de travail" in q for q in expansion_queries)
    # expansion only ADDS queries: the user's original terms stay untouched
    assert plan.sub_questions[0] == query
    assert _keyword_queries(plan)[0] == query


def test_heuristic_plan_unchanged_without_lexicon_hits():
    query = "Quelle est la météo à Ouagadougou demain ?"
    plan = heuristic_plan(query)
    assert "terminology expansion" not in plan.rationale
    assert _keyword_queries(plan) == [query]


def test_heuristic_plan_expansion_tasks_capped():
    query = "Quelles règles pour le licenciement, le divorce, la succession, le bail et l'impôt ?"
    plan = heuristic_plan(query)
    expansions = expand_terms(query)
    assert len(expansions) > _MAX_EXPANSION_TASKS  # sanity: the cap is what binds
    assert len(_expansion_queries(plan)) == _MAX_EXPANSION_TASKS


def test_heuristic_plan_expansion_deduped_against_existing_keyword_tasks():
    plan = heuristic_plan("Quels sont les droits d'un salarié licencié au Burkina Faso ?")
    keyword_queries = _keyword_queries(plan)
    assert len(keyword_queries) == len(set(keyword_queries))


def test_expand_legal_terms_tool_registered_for_llm_planner():
    assert "expand_legal_terms" in {t.name for t in list_tools()}
