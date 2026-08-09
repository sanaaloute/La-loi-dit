"""Planning tools: language detection, date extraction, domain classification
and retrieval plan construction.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.tools.base import tool
from backend.agents.tools.registry import register_tool


class DetectLanguageArgs(BaseModel):
    text: str


class ExtractScenarioDateArgs(BaseModel):
    text: str


class ClassifyLegalDomainsArgs(BaseModel):
    query: str


class BuildSubQuestionsArgs(BaseModel):
    query: str


class BuildSearchTasksArgs(BaseModel):
    query: str
    sub_questions: list[str] = Field(default_factory=list)
    legal_domains: list[str] = Field(default_factory=list)
    top_k: int = 8
    aux_top_k: int = 5


class ExpandLegalTermsArgs(BaseModel):
    query: str


_FR_MARKERS = (" le ", " la ", " les ", " de ", " du ", " des ", " est ", " quelle", " quel ", " au ", " aux ")

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


@tool("detect_language", "Detect whether the query is written in French or English.")
async def detect_language(ctx: Any, state: Any, args: DetectLanguageArgs) -> str:
    lowered = f" {args.text.lower()} "
    return "fr" if any(m in lowered for m in _FR_MARKERS) else "en"


@tool("extract_scenario_date", "Extract an ISO date (YYYY-MM-DD) from the query if present.")
async def extract_scenario_date(ctx: Any, state: Any, args: ExtractScenarioDateArgs) -> Optional[str]:
    import re

    text = args.text
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


@tool("classify_legal_domains", "Classify the query into one or more legal domains (constitution, labor_code, etc.).")
async def classify_legal_domains(ctx: Any, state: Any, args: ClassifyLegalDomainsArgs) -> list[str]:
    q = args.query.lower()
    return [d for d, kws in _DOMAIN_KEYWORDS.items() if any(k in q for k in kws)]


@tool("build_sub_questions", "Break the user question into focused sub-questions for retrieval.")
async def build_sub_questions(ctx: Any, state: Any, args: BuildSubQuestionsArgs) -> list[str]:
    # Default deterministic decomposition: keep the question plus one reformulation.
    return [args.query, f"Quelles sont les dispositions légales applicables à : {args.query}?"]


@tool("build_search_tasks", "Build the search tasks (vector, keyword, official sources) for a retrieval plan.")
async def build_search_tasks(ctx: Any, state: Any, args: BuildSearchTasksArgs) -> list[dict[str, Any]]:
    from backend.core.models import SearchKind, SearchTask

    tasks: list[SearchTask] = [
        SearchTask(kind=SearchKind.VECTOR, query=args.query, top_k=args.top_k),
        SearchTask(kind=SearchKind.KEYWORD, query=args.query, top_k=args.top_k),
    ]
    q = args.query.lower()
    if any(w in q for w in ("loi", "décret", "decret", "officiel", "gouvernement", "journal officiel")):
        tasks.append(SearchTask(kind=SearchKind.GOVERNMENT, query=args.query, top_k=args.aux_top_k))
    if any(w in q for w in ("règlement", "reglement", "arrêté", "arrete", "réglementation")):
        tasks.append(SearchTask(kind=SearchKind.REGULATION, query=args.query, top_k=args.aux_top_k))
    if any(w in q for w in ("jurisprudence", "cour ", "cassation", "tribunal")):
        tasks.append(SearchTask(kind=SearchKind.CASE_LAW, query=args.query, top_k=args.aux_top_k))
    if any(w in q for w in ("récent", "recent", "actuellement", "news", "actualité", "2024", "2025", "2026")):
        tasks.append(SearchTask(kind=SearchKind.NEWS, query=args.query, top_k=args.aux_top_k))

    filters: dict[str, Any] = {}
    if args.legal_domains:
        filters["legal_domains"] = args.legal_domains
    for t in tasks:
        t.filters = {**t.filters, **filters}

    return [t.model_dump(mode="json") for t in tasks]


@tool(
    "expand_legal_terms",
    "Expand legal terms found in the query with synonyms and related terms from the "
    "legal terminology lexicon (recall-oriented; never replaces the original terms).",
)
async def expand_legal_terms(ctx: Any, state: Any, args: ExpandLegalTermsArgs) -> dict[str, list[str]]:
    from backend.planner.terminology import expand_terms

    return expand_terms(args.query)


register_tool(detect_language)
register_tool(extract_scenario_date)
register_tool(classify_legal_domains)
register_tool(build_sub_questions)
register_tool(build_search_tasks)
register_tool(expand_legal_terms)
