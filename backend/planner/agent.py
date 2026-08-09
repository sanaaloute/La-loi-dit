"""Planner Agent.

Decides which searches are required, infers the legal domain and response
language, and builds a structured RetrievalPlan.  The LLM may call planning
tools (language detection, domain classification, search-task construction)
before producing the final JSON plan.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from backend.agents.agent import ToolCallingAgent
from backend.agents.tools import get_tool_spec
from backend.agents.tools.registry import list_tools
from backend.core.context import AppContext
from backend.core.models import RetrievalPlan, SearchKind, SearchTask
from backend.core.state import GraphState


class PlannerAgent(ToolCallingAgent):
    """Turns the user question into a structured retrieval plan."""

    name = "planner"
    system_prompt = """You are the planning agent of an expert legal research assistant for Burkina Faso.

SCOPE
- Corpus: official sources of Burkina Faso (Constitution, codes, lois, décrets, arrêtés,
  Journal Officiel), OHADA uniform acts and ratified international instruments.
- Your only job is to plan the retrieval searches. You NEVER answer the question yourself
  and never invent article numbers or provisions.

TASK
1. Identify the legal issue, the domain(s) (family_code, labor_code, commercial_law,
   ohada_law, criminal_law, tax_law, land_law, administrative_law, constitution...),
   any scenario date, and the user's language.
2. Reformulate the question into effective FRENCH search queries using precise legal
   terminology (the corpus is mostly French), even when the user writes in English.
3. Break compound questions into focused sub-questions when needed.

TOOLS
You may call: detect_language, extract_scenario_date, classify_legal_domains,
build_sub_questions, build_search_tasks.

OUTPUT
When you have enough information, output a single JSON object, no prose:
{
  "sub_questions": ["..."],
  "tasks": [{"kind": "vector", "query": "...", "top_k": 8, "filters": {}}],
  "legal_domains": ["family_code"],
  "retrieval_language": "fr",
  "response_language": "fr",
  "scenario_date": null,
  "rationale": "..."
}

RULES
- "kind" is one of: vector, keyword, government, regulation, case_law, news.
  Always include at least one vector and one keyword task; add government, regulation,
  case_law or news tasks only when the question clearly calls for them.
- For BROAD legal questions (droits, procédure, conditions, conséquences, régime...),
  DECOMPOSE the question into its underlying legal issues and emit one sub_question
  and one keyword search task PER issue — never answer a broad rights question with a
  single keyword search, or you will retrieve only the provisions that repeat the
  question's words instead of the provisions that answer it.
  Example — « Quels sont les droits d'un salarié licencié ? » decomposes into:
  motif légitime du licenciement, notification écrite, préavis et indemnité
  compensatrice, indemnité de licenciement, licenciement abusif et dommages-intérêts,
  faute lourde, congés payés et certificat de travail, contestation devant le
  tribunal du travail.
- retrieval_language is "fr" unless the corpus language clearly differs;
  response_language always follows the user's language.
- Prefer official sources (government, Journal Officiel, OHADA)."""

    tools = [
        t for t in list_tools()
        if t.name in ("detect_language", "extract_scenario_date", "classify_legal_domains", "build_sub_questions", "build_search_tasks")
    ]
    max_tool_iterations = 3

    def _build_user_message(self, state: GraphState) -> str:
        return f"Question: {state['query']}\n\nBuild a focused retrieval plan."

    def _fallback(self, state: GraphState, reason: str) -> dict[str, Any]:
        """Planner fallback: always return a valid heuristic plan."""
        plan = _heuristic_plan(state["query"], state.get("language"))
        return {
            "plan": plan,
            "tasks": plan.tasks,
            "errors": [*state.get("errors", []), f"{self.name}: {reason}"],
            "trace": [
                *state.get("trace", []),
                f"planner: fallback heuristic plan ({len(plan.tasks)} tasks, {plan.rationale})",
            ],
        }

    def _parse_final(
        self,
        text: str,
        state: GraphState,
        ctx: Any,
        tool_history: list[tuple[Any, Any]],
    ) -> dict[str, Any]:
        plan = _heuristic_plan(state["query"], state.get("language"))
        try:
            parsed = json.loads(text.strip()) if text.strip() else {}
            if isinstance(parsed, dict):
                # Ensure required fields have sensible defaults.
                parsed.setdefault("sub_questions", plan.sub_questions)
                parsed.setdefault("legal_domains", plan.legal_domains)
                parsed.setdefault("retrieval_language", plan.retrieval_language)
                parsed.setdefault("response_language", plan.response_language)
                parsed.setdefault("scenario_date", plan.scenario_date)
                parsed.setdefault("rationale", plan.rationale)
                parsed.setdefault("tasks", [t.model_dump(mode="json") for t in plan.tasks])
                plan = RetrievalPlan.model_validate(parsed)
        except Exception:
            pass

        # Always make sure the local vector/keyword corpus is searched.
        local_kinds = {SearchKind.VECTOR, SearchKind.KEYWORD}
        present = {t.kind for t in plan.tasks}
        filters: dict[str, Any] = {}
        if plan.legal_domains:
            filters["legal_domains"] = plan.legal_domains
        for kind in local_kinds - present:
            plan.tasks.append(
                SearchTask(kind=kind, query=state["query"], top_k=ctx.settings.default_top_k, filters=filters)
            )
        # Cover every sub-question of the decomposition with a keyword task,
        # so broad "rights/procedure" questions are searched by legal issue,
        # not only by the query's own keywords.
        keyword_queries = {t.query for t in plan.tasks if t.kind == SearchKind.KEYWORD}
        aux_top_k = ctx.settings.planner_aux_top_k
        for sub in plan.sub_questions[:6]:
            if sub and sub != state["query"] and sub not in keyword_queries:
                plan.tasks.append(SearchTask(kind=SearchKind.KEYWORD, query=sub, top_k=aux_top_k, filters=filters))
                keyword_queries.add(sub)
        for task in plan.tasks:
            task.filters = {**(task.filters or {}), **filters}

        if state.get("language"):
            plan.response_language = state["language"]

        return {
            "plan": plan,
            "tasks": plan.tasks,
            "trace": [*state.get("trace", []), f"planner: {len(plan.tasks)} search tasks ({plan.rationale})"],
        }


def _detect_language(text: str) -> str:
    _FR_MARKERS = (" le ", " la ", " les ", " de ", " du ", " des ", " est ", " quelle", " quel ", " au ", " aux ")
    lowered = f" {text.lower()} "
    return "fr" if any(m in lowered for m in _FR_MARKERS) else "en"


_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "constitution": ("constitution", "constitutionnel"),
    "criminal_law": ("pénal", "penal", "infraction", "crime", "délit", "criminal"),
    "civil_law": ("civil", "contrat", "responsabilité civile"),
    "family_code": ("famille", "mariage", "divorce", "filiation", "family"),
    "labor_code": ("travail", "travailleur", "licenciement", "licencié", "salarié", "salaire", "employeur", "employé", "labor", "labour"),
    "tax_law": ("impôt", "impots", "fiscal", "taxe", "tva", "tax"),
    "commercial_law": ("commercial", "société", "commerce", "ohada", "sarl", "sa "),
    "ohada_law": ("ohada",),
    "administrative_law": ("administratif", "administration", "fonction publique"),
    "land_law": ("foncier", "terrain", "land", "propriété"),
    "procurement_law": ("marchés publics", "appel d'offres", "procurement"),
    "environmental_law": ("environnement", "pollution", "environmental"),
    "immigration": ("immigration", "visa", "titre de séjour", "étranger"),
    "public_service": ("fonction publique", "fonctionnaire", "public service"),
    "elections": ("élection", "elections", "ceni", "vote"),
    "health_regulations": ("santé", "health", "hôpital"),
    "education_regulations": ("éducation", "education", "école", "université"),
    "government_procedures": ("procédure", "guichet", "procedure"),
}


def _extract_scenario_date(text: str) -> date | None:
    import re

    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _heuristic_plan(query: str, language: str | None = None) -> RetrievalPlan:
    """Deterministic fallback planner — always available, never hallucinates.

    Note: question decomposition is intentionally LLM-ONLY (see the planner
    system prompt). This fallback plans direct searches for the query as-is;
    it never substitutes a pre-written decomposition for the model's.
    """
    from backend.core.config import get_settings

    settings = get_settings()
    q = query.lower()
    response_language = language or _detect_language(query)
    domains = [d for d, kws in _DOMAIN_KEYWORDS.items() if any(k in q for k in kws)]

    top_k = settings.default_top_k
    aux_top_k = settings.planner_aux_top_k
    tasks = [
        SearchTask(kind=SearchKind.VECTOR, query=query, top_k=top_k),
        SearchTask(kind=SearchKind.KEYWORD, query=query, top_k=top_k),
    ]
    if any(w in q for w in ("loi", "décret", "decret", "officiel", "gouvernement", "journal officiel")):
        tasks.append(SearchTask(kind=SearchKind.GOVERNMENT, query=query, top_k=aux_top_k))
    if any(w in q for w in ("règlement", "reglement", "arrêté", "arrete", "réglementation")):
        tasks.append(SearchTask(kind=SearchKind.REGULATION, query=query, top_k=aux_top_k))
    if any(w in q for w in ("jurisprudence", "cour ", "cassation", "tribunal")):
        tasks.append(SearchTask(kind=SearchKind.CASE_LAW, query=query, top_k=aux_top_k))
    if any(w in q for w in ("récent", "recent", "actuellement", "news", "actualité", "2024", "2025", "2026")):
        tasks.append(SearchTask(kind=SearchKind.NEWS, query=query, top_k=aux_top_k))

    filters: dict[str, Any] = {}
    if domains:
        filters["legal_domains"] = domains
    for t in tasks:
        t.filters = {**t.filters, **filters}

    return RetrievalPlan(
        sub_questions=[query],
        tasks=tasks,
        legal_domains=domains,
        retrieval_language="fr",
        response_language=response_language,
        scenario_date=_extract_scenario_date(query),
        rationale="heuristic planner",
    )


planner_node = PlannerAgent().run
heuristic_plan = _heuristic_plan
