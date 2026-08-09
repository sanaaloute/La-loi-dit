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
# Canonical French markers / domain keywords live with the planning tools
# (also used by the detect_language / classify_legal_domains tools).
from backend.agents.tools.planning import _DOMAIN_KEYWORDS, _FR_MARKERS
from backend.agents.tools.registry import list_tools
from backend.core.context import AppContext
from backend.core.models import RetrievalPlan, SearchKind, SearchTask
from backend.core.prompts import PromptRef
from backend.core.state import GraphState
from backend.planner.decomposition import deterministic_decompose
from backend.planner.question_types import classify_question_type, detect_temporal_intent
from backend.planner.terminology import expand_terms


class PlannerAgent(ToolCallingAgent):
    """Turns the user question into a structured retrieval plan."""

    name = "planner"
    # Resolved through the prompt registry (backend.core.prompts.PLANNER_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("PLANNER_SYSTEM")

    tools = [
        t for t in list_tools()
        if t.name in ("detect_language", "extract_scenario_date", "classify_legal_domains", "build_sub_questions", "build_search_tasks", "expand_legal_terms")
    ]
    def _tool_iteration_budget(self, ctx: AppContext) -> int:
        """Tool-loop budget sourced from ``settings.planner_max_tool_iterations``."""
        return ctx.settings.planner_max_tool_iterations

    def _build_user_message(self, state: GraphState, ctx: Any = None) -> str:
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
        plan = _heuristic_plan(state["query"], state.get("language"), settings=ctx.settings)
        try:
            parsed = json.loads(text.strip()) if text.strip() else {}
            if isinstance(parsed, dict):
                # Ensure required fields have sensible defaults.
                parsed.setdefault("sub_questions", plan.sub_questions)
                parsed.setdefault("legal_domains", plan.legal_domains)
                parsed.setdefault("retrieval_language", plan.retrieval_language)
                parsed.setdefault("response_language", plan.response_language)
                parsed.setdefault("scenario_date", plan.scenario_date)
                # Deterministic classification; the LLM output overrides when
                # it provides its own question_type / temporal_intent.
                parsed.setdefault("question_type", plan.question_type)
                parsed.setdefault("temporal_intent", plan.temporal_intent)
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
        for sub in plan.sub_questions[: ctx.settings.planner_max_sub_question_tasks]:
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
    lowered = f" {text.lower()} "
    return "fr" if any(m in lowered for m in _FR_MARKERS) else "en"


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


def _heuristic_plan(query: str, language: str | None = None, settings: Any = None) -> RetrievalPlan:
    """Deterministic fallback planner — always available, never hallucinates.

    Broad questions (rights, obligations, procedure...) are decomposed into
    their underlying legal issues via the curated taxonomy in
    ``backend.planner.decomposition``, with one keyword search task per issue;
    everything else plans direct searches for the query as-is.  ``settings``
    defaults to the process-wide ``get_settings()``.
    """
    from backend.core.config import get_settings

    settings = settings or get_settings()
    q = query.lower()
    response_language = language or _detect_language(query)
    domains = [d for d, kws in _DOMAIN_KEYWORDS.items() if any(k in q for k in kws)]
    question_type = classify_question_type(query, response_language)
    temporal_intent = detect_temporal_intent(query)

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

    sub_questions = [query]
    rationale = "heuristic planner"
    sub_issues = deterministic_decompose(query, question_type, domains)
    if sub_issues:
        sub_questions.extend(sub_issues)
        for issue in sub_issues:
            tasks.append(SearchTask(kind=SearchKind.KEYWORD, query=issue, top_k=aux_top_k))
        rationale = "heuristic planner (deterministic decomposition)"

    # Legal terminology expansion (spec §14/§29): one extra keyword task per
    # matched term group, built from its synonyms + related terms.  Expansion
    # only ADDS recall-oriented queries; the user's original terms are never
    # rewritten or replaced.
    expansions = expand_terms(query)
    if expansions:
        keyword_queries = {t.query for t in tasks if t.kind == SearchKind.KEYWORD}
        expanded: list[str] = []
        for canonical, terms in list(expansions.items())[: settings.planner_max_expansion_tasks]:
            expansion_query = " ".join(terms)
            if expansion_query and expansion_query not in keyword_queries:
                tasks.append(SearchTask(kind=SearchKind.KEYWORD, query=expansion_query, top_k=aux_top_k))
                keyword_queries.add(expansion_query)
                expanded.append(canonical)
        if expanded:
            rationale += f"; terminology expansion ({', '.join(expanded)})"

    filters: dict[str, Any] = {}
    if domains:
        filters["legal_domains"] = domains
    for t in tasks:
        t.filters = {**t.filters, **filters}

    return RetrievalPlan(
        sub_questions=sub_questions,
        tasks=tasks,
        legal_domains=domains,
        retrieval_language="fr",
        response_language=response_language,
        scenario_date=_extract_scenario_date(query),
        question_type=question_type,
        temporal_intent=temporal_intent,
        rationale=rationale,
    )


planner_node = PlannerAgent().run
heuristic_plan = _heuristic_plan
