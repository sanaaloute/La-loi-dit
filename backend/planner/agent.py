"""Planner Agent.

Decides which searches are required (vector, keyword, web, government,
case law, news, regulation, uploaded docs) and in which language to
retrieve (evidence is mostly French) versus respond (user's language).
LLM-planned when a provider is configured; deterministic heuristic
planner otherwise and on any LLM failure (planning retry budget = 1,
enforced by the single corrective retry inside `complete_json`).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from backend.core.config import get_settings
from backend.core.constants import LEGAL_DOMAINS
from backend.core.context import AppContext
from backend.core.models import RetrievalPlan, SearchKind, SearchTask
from backend.core.state import GraphState

_SYSTEM = """You are the planning agent of a legal research assistant for Burkina Faso.
Plan the searches required to answer the user's legal question. Prefer official
sources (government, official gazette, OHADA). Retrieval language is usually
French; response language follows the user's language. Output a retrieval plan."""

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "constitution": ("constitution", "constitutionnel"),
    "criminal_law": ("pénal", "penal", "infraction", "crime", "délit", "criminal"),
    "civil_law": ("civil", "contrat", "responsabilité civile"),
    "family_code": ("famille", "mariage", "divorce", "filiation", "family"),
    "labor_code": ("travail", "licenciement", "salaire", "employeur", "labor", "labour"),
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

_FR_MARKERS = (" le ", " la ", " les ", " de ", " du ", " des ", " est ", " quelle", " quel ", " au ", " aux ")


def _detect_language(text: str) -> str:
    lowered = f" {text.lower()} "
    return "fr" if any(m in lowered for m in _FR_MARKERS) else "en"


def _extract_scenario_date(text: str) -> date | None:
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


def heuristic_plan(query: str, language: str | None = None) -> RetrievalPlan:
    """Deterministic planner — always available, never hallucinates."""
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


async def planner_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    query = state["query"]
    language = state.get("language")
    plan: RetrievalPlan
    errors: list[str] = []
    if ctx.llm.provider == "mock":
        plan = heuristic_plan(query, language)
    else:
        try:
            plan = await ctx.llm.complete_json(_SYSTEM, query, RetrievalPlan)
            if not plan.tasks:
                plan = heuristic_plan(query, language)
        except Exception as exc:  # LLM failure => deterministic fallback
            errors.append(f"planner_llm_fallback: {exc}")
            plan = heuristic_plan(query, language)
    if state.get("scenario_date") and not plan.scenario_date:
        try:
            plan.scenario_date = date.fromisoformat(state["scenario_date"])
        except ValueError:
            pass
    if language:
        plan.response_language = language
    update: dict[str, Any] = {
        "plan": plan,
        "tasks": plan.tasks,
        "trace": [*state.get("trace", []), f"planner: {len(plan.tasks)} search tasks ({plan.rationale})"],
    }
    if errors:
        update["errors"] = [*state.get("errors", []), *errors]
    return update
