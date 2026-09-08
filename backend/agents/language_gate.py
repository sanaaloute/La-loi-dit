"""Language Gate.

Runs after the input guardrail and before the query router.  The legal corpus
is French-only, so a query written in another language cannot retrieve
anything: this node detects the query language and translates non-French
queries into French for the whole downstream pipeline (router, planner,
retrieval), keeping the user's original wording in ``original_query`` and the
detected language code in ``language`` so the final answer comes back in the
user's own language.

Fail-safe by design (same policy as the query router): any LLM error or
unparseable translation leaves the original query untouched.  French queries
are detected with a cheap heuristic and never cost an LLM call.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.agents.agent import Agent
# Canonical French markers live with the planning tools (also used by the
# planner's heuristic fallback).
from backend.agents.tools.planning import _FR_MARKERS
from backend.core.context import AppContext
from backend.core.prompts import PromptRef
from backend.core.state import GraphState

# French diacritics catch marker-less French ("Préavis en cas de licenciement ?").
_FR_DIACRITICS = frozenset("àâäéèêëîïôöùûüç")


def _looks_french(text: str) -> bool:
    lowered = f" {text.lower()} "
    return any(m in lowered for m in _FR_MARKERS) or any(
        c in _FR_DIACRITICS for c in lowered
    )


def _parse_translation(raw: str) -> Optional[dict[str, str]]:
    """Parse the translator's JSON output; None on anything unusable."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    french = payload.get("french")
    language = payload.get("language")
    if not isinstance(french, str) or not french.strip():
        return None
    return {
        "french": french.strip(),
        "language": language.strip().lower() if isinstance(language, str) else "",
    }


class LanguageGateAgent(Agent):
    """Translates non-French queries to French before routing and retrieval."""

    name = "language_gate"
    # Resolved through the prompt registry (backend.core.prompts.QUERY_TRANSLATE_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("QUERY_TRANSLATE_SYSTEM")

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        query = state.get("query", "")
        language = state.get("language") or ""
        trace = state.get("trace", [])
        if not query.strip() or _looks_french(query):
            return {
                "language": language or "fr",
                "trace": [*trace, "language_gate: fr (heuristic)"],
            }
        try:
            raw = await ctx.llm.complete(
                self.system_prompt, f"Texte: {query}", temperature=0.0
            )
            payload = _parse_translation(raw or "")
        except Exception:
            payload = None
        if payload is None:
            # Fail-safe: keep the original query rather than blocking the turn.
            return {
                "language": language or "fr",
                "trace": [*trace, "language_gate: translation unavailable, query kept"],
            }
        detected = payload["language"]
        # An explicit non-French client choice wins over detection; the "fr"
        # default the clients always send does not.
        return {
            "query": payload["french"],
            "original_query": query,
            "language": (language if language and language != "fr" else detected) or "fr",
            "trace": [
                *trace,
                f"language_gate: translated {detected or 'unknown'} -> fr",
            ],
        }


language_gate_node = LanguageGateAgent().run
