"""Reasoning Agent.

Reads the ranked evidence, identifies what is established, what is missing and
any contradictions.  May request one bounded retrieval retry when evidence is
insufficient.  This is a completion agent: the LLM reasons in prose and the
node translates the prose into state updates.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.agents.agent import CompletionAgent
from backend.agents.context_agent import format_memory_sections
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.prompts import PromptRef
from backend.core.state import GraphState
from backend.ingestion.text_cleaning import repair_extraction_artifacts
from backend.tools.legal_calculations import (
    RuleNotFound,
    compute_notice_period,
    compute_severance,
)


def _deterministic_calculations(query: str) -> str:
    """Run deterministic legal calculators when the query supplies concrete inputs."""
    q = query.lower()
    if not any(k in q for k in ("licenci", "préavis", "preavis", "indemnit", "anciennet")):
        return ""
    notes: list[str] = []

    seniority_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:ans?|ann[ée]es?)\b", q)
    seniority = float(seniority_match.group(1).replace(",", ".")) if seniority_match else None

    salary_match = re.search(r"\b(\d[\d\s.,]{2,})\s*(?:fcfa|f\s*cfa|cfa|francs?)\b", q)
    salary: Optional[float] = None
    if salary_match:
        raw_num = re.sub(r"\s+", "", salary_match.group(1)).replace(",", ".")
        try:
            salary = float(raw_num)
        except ValueError:
            salary = None

    if salary is not None and seniority is not None and any(k in q for k in ("indemnit", "licenci")):
        try:
            res = compute_severance(monthly_salary=salary, seniority_years=seniority)
            notes.append(f"CALCUL DÉTERMINISTE (indemnité de licenciement) : {res.explanation}")
        except (RuleNotFound, ValueError):
            pass

    for cat in ("cadre", "agent de maîtrise", "agent de maitrise", "technicien", "employé", "employe", "ouvrier", "journalier"):
        if cat in q and any(k in q for k in ("préavis", "preavis", "licenci")):
            try:
                res = compute_notice_period(category=cat, seniority_years=seniority or 0.0)
                notes.append(f"CALCUL DÉTERMINISTE (préavis) : {res.explanation}")
                break
            except (RuleNotFound, ValueError):
                pass

    return "\n".join(notes)


class ReasoningAgent(CompletionAgent):
    """Reasons over the ranked evidence and signals missing evidence."""

    name = "reasoning_agent"
    # Resolved through the prompt registry (backend.core.prompts.REASONING_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("REASONING_SYSTEM")

    def _build_user_message(self, state: GraphState, ctx: Optional[AppContext] = None) -> str:
        settings = ctx.settings if ctx is not None else get_settings()
        memory_sections = format_memory_sections(state, max_entry_chars=settings.context_message_max_chars)
        # History goes last (same anti-contamination framing as the response
        # generator): the question and the evidence lead the prompt.
        suffix = (
            "\n\n---\nCONTEXTE DE CONVERSATION (context only — do not repeat or "
            "continue it / ne pas répéter):\n"
            f"{memory_sections}"
        ) if memory_sections else ""
        evidence = list(state.get("ranked_evidence", []))
        if not evidence:
            return f"Question: {state['query']}\n\nNo evidence was retrieved.{suffix}"
        # ranked_evidence is already bounded per sub-question by the ranking
        # node; every kept chunk reaches the prompt.
        evidence_text = "\n\n".join(
            f"[{i}] {c.citation_label()} ({c.publication_date or 'date inconnue'}): "
            f"{repair_extraction_artifacts(c.content)[: settings.reasoning_max_excerpt_chars]}"
            for i, c in enumerate(evidence, start=1)
        )
        return f"Question: {state['query']}\n\nPreuves:\n{evidence_text}{suffix}"

    def _parse_final(self, text: str, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        retries = state.get("retrieval_retries", 0)
        max_retries = ctx.settings.max_retrieval_retries
        wants_retry = text.strip().lower().startswith("insufficient:")
        # Honor the retry only when evidence is genuinely thin or coverage is
        # low: re-fetching the same tasks otherwise returns the same chunks
        # and doubles retrieval latency for nothing (production trace: retry
        # requested with 18 ranked chunks and 89% coverage).
        evidence = list(state.get("ranked_evidence", []))
        coverage_report = state.get("coverage_report")
        thin = len(evidence) <= ctx.settings.reasoning_retry_max_evidence
        low_coverage = (
            coverage_report is not None
            and coverage_report.coverage < ctx.settings.coverage_retry_threshold
        )
        needs_more = wants_retry and (thin or low_coverage)
        calc_notes = _deterministic_calculations(state.get("original_query") or state.get("query", ""))
        notes_text = text.strip()
        if calc_notes:
            notes_text = f"{notes_text}\n\n{calc_notes}" if notes_text else calc_notes

        if needs_more and retries < max_retries and not state.get("needs_more_retrieval"):
            return {
                "needs_more_retrieval": True,
                "tasks": state.get("plan", None) and state["plan"].tasks or state.get("tasks", []),
                "reasoning_notes": notes_text,
                "trace": [*state.get("trace", []), "reasoning: insufficient evidence, retry requested"],
            }
        return {
            "reasoning_notes": notes_text,
            "needs_more_retrieval": False,
            "trace": [
                *state.get("trace", []),
                f"reasoning: analyzed {len(state.get('ranked_evidence', []))} evidence chunks"
                + (" (+ deterministic calc)" if calc_notes else "")
                + (" (retry declined: evidence sufficient)" if wants_retry else ""),
            ],
        }


reasoning_agent_node = ReasoningAgent().run
