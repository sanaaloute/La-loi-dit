"""Reasoning Agent.

Never answers immediately: reads the ranked evidence, ignores weak items,
identifies missing information and may request one more retrieval pass
(bounded by MAX_RETRIEVAL_RETRIES). It reasons only from verified evidence;
with no evidence it says so instead of guessing (grounded answer policy).
"""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.state import GraphState

_SYSTEM = """You are the reasoning agent of a legal research assistant for Burkina Faso.
Read the evidence excerpts below. Reason ONLY from this verified evidence.
Identify what is established, what is missing, and any contradictions.
Do not invent legal provisions that are not in the evidence."""


async def reasoning_agent_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    evidence = list(state.get("ranked_evidence", []))
    retries = state.get("retrieval_retries", 0)
    max_retries = ctx.settings.max_retrieval_retries
    update: dict[str, Any] = {}

    if not evidence:
        if retries < max_retries and not state.get("needs_more_retrieval"):
            # one bounded retry with the raw query before giving up
            update = {
                "needs_more_retrieval": True,
                "tasks": state.get("plan", None) and state["plan"].tasks or state.get("tasks", []),
                "reasoning_notes": "Aucune preuve pertinente: nouvelle tentative de recherche (1/1).",
            }
        else:
            update = {
                "needs_more_retrieval": False,
                "reasoning_notes": (
                    "Aucune preuve vérifiable trouvée dans les sources indexées. "
                    "La réponse doit déclarer explicitement l'insuffisance des preuves."
                ),
            }
        update["trace"] = [*state.get("trace", []), "reasoning: insufficient evidence"]
        return update

    evidence_text = "\n\n".join(
        f"[{i}] {c.citation_label()} ({c.publication_date or 'date inconnue'}): {c.content[:600]}"
        for i, c in enumerate(evidence[:10], start=1)
    )
    notes: str
    if ctx.llm.provider == "mock":
        notes = "Analyse fondée exclusivement sur les extraits de preuve fournis."
    else:
        try:
            notes = await ctx.llm.complete(_SYSTEM, f"Question: {state['query']}\n\nPreuves:\n{evidence_text}")
        except Exception as exc:
            notes = f"Analyse heuristique (LLM indisponible: {exc})."
    return {
        "reasoning_notes": notes,
        "needs_more_retrieval": False,
        "trace": [*state.get("trace", []), f"reasoning: analyzed {min(len(evidence), 10)} evidence chunks"],
    }
