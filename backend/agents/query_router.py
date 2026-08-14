"""Query Router Agent.

Runs right after the input guardrail and decides whether a query needs the
legal retrieval pipeline at all.  Short greetings, thanks, goodbyes and meta
questions about the assistant are answered directly by the response
generator (``route == "direct"``); everything else follows the full
planner/retrieval/reasoning path (``route == "retrieval"``).

Fail-safe by design: any doubt, LLM error or unparseable classification
routes to retrieval, so a possible legal question never loses its grounding.
The router only ever runs on guardrail-approved queries and never touches
the guardrail result — it cannot unblock a blocked query.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from backend.agents.agent import Agent
from backend.core.context import AppContext
from backend.core.prompts import PromptRef
from backend.core.state import GraphState

# Only short messages qualify for the deterministic direct route: a long
# message that merely starts with "bonjour" usually carries a real question.
DIRECT_MAX_CHARS = 120

# Conversational clauses (FR + EN), matched with fullmatch against each
# clause of the normalized message.  EVERY clause must match for the
# deterministic direct route, so "bonjour, quels sont mes droits ?" stays
# on the retrieval path.
_DIRECT_CLAUSE_PATTERNS = (
    # greetings / how-are-you
    r"bonjour",
    r"bonsoir",
    r"salut",
    r"coucou",
    r"hello",
    r"hi",
    r"hey",
    r"good (morning|afternoon|evening|day)",
    r"(ça|ca) va",
    r"comment (ça|ca) va",
    r"comment vas-tu",
    r"comment allez-vous",
    r"how are you( doing)?",
    # thanks
    r"merci( beaucoup| bien)?",
    r"thanks?( (you|a lot|so much))?",
    # goodbyes
    r"au revoir",
    r"adieu",
    r"à bientôt",
    r"a bientot",
    r"bye( bye)?",
    r"goodbye",
    r"bonne (journée|journee|soirée|soiree|nuit)",
    # meta questions about the assistant
    r"qui (es-tu|es tu|êtes-vous|etes-vous)",
    r"que (peux-tu|pouvez-vous|sais-tu|savez-vous) faire",
    r"qu'est-ce que tu (peux|sais) faire",
    r"que fais-tu",
    r"comment (tu fonctionnes|fonctionnes-tu|vous fonctionnez|fonctionnez-vous)",
    r"présente-toi",
    r"presente-toi",
    r"who are you",
    r"what are you",
    r"what can you do",
    r"how do you work",
    r"how does this (work|app|assistant)( work)?",
    r"what is this (app|application|platform|assistant)",
    r"help",
    r"aide",
)
_DIRECT_CLAUSES = tuple(re.compile(p, re.IGNORECASE) for p in _DIRECT_CLAUSE_PATTERNS)

_CLAUSE_SPLIT = re.compile(r"[,;:!?.\n…]+")


def _normalize(text: str) -> str:
    """Lowercase, NFC-normalize and collapse whitespace for pattern matching."""
    text = unicodedata.normalize("NFC", text.strip().lower())
    return re.sub(r"\s+", " ", text)


def is_direct_shortcut(query: str) -> bool:
    """Deterministic direct-route pre-pass (no LLM).

    True only for short messages whose every clause is a greeting, a thanks,
    a goodbye or a meta question about the assistant.
    """
    if not query or len(query) > DIRECT_MAX_CHARS:
        return False
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(_normalize(query))]
    clauses = [c for c in clauses if c]
    return bool(clauses) and all(
        any(pattern.fullmatch(clause) for pattern in _DIRECT_CLAUSES) for clause in clauses
    )


def parse_route(text: str) -> str:
    """Parse the classifier's one-word output; anything ambiguous is retrieval."""
    tokens = text.strip().split()
    if not tokens:
        return "retrieval"
    token = tokens[0].strip(".!:»«\"'").upper()
    return "direct" if token == "DIRECT" else "retrieval"


class QueryRouterAgent(Agent):
    """Routes guardrail-approved queries to the direct or retrieval path."""

    name = "query_router"
    # Resolved through the prompt registry (backend.core.prompts.QUERY_ROUTER_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("QUERY_ROUTER_SYSTEM")

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        query = state.get("query", "")
        if is_direct_shortcut(query):
            route, method = "direct", "patterns"
        else:
            route, method = await self._classify(state, ctx), "llm"
        return {
            "route": route,
            "trace": [*state.get("trace", []), f"query_router: {route} ({method})"],
        }

    async def _classify(self, state: GraphState, ctx: AppContext) -> str:
        """LLM classification; every failure mode falls back to retrieval."""
        try:
            text = await ctx.llm.complete(
                self.system_prompt,
                f"Question: {state.get('query', '')}",
                temperature=0.0,
            )
        except Exception:
            return "retrieval"
        return parse_route(text)


query_router_node = QueryRouterAgent().run
