"""Conflict Resolver Agent.

When two sources disagree, resolution follows the spec's order:
1. prefer official government source (authority weight),
2. prefer the latest law / amendment (publication & effective dates),
3. legal timeline reasoning: for a scenario date, prefer the version in force.

Uses the ``detect_contradictions`` and ``resolve_conflict_by_authority`` tools.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from backend.agents.agent import Agent
from backend.agents.tools import get_tool_spec
from backend.agents.tools.registry import list_tools
from backend.core.config import get_settings
from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.context import AppContext
from backend.core.models import ConflictReport, EvidenceChunk
from backend.core.state import GraphState


_FR_NUMBER_WORDS = {
    "un", "une", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
    "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    "vingt", "trente", "quarante", "cinquante", "soixante", "cent", "mille",
}


def _conflict_key(chunk: EvidenceChunk) -> Optional[tuple[str, str]]:
    if chunk.document_name and chunk.article:
        return (chunk.document_name.lower().strip(), str(chunk.article).strip())
    return None


def _claims(chunk: EvidenceChunk) -> set[str]:
    text = chunk.content.lower()
    claims = set(re.findall(r"\d+(?:[.,]\d+)?", text))
    claims |= {
        w for w in re.findall(r"[a-zàâäéèêëîïôöùûüç]+", text) if w in _FR_NUMBER_WORDS
    }
    return claims


def _contradict(a: EvidenceChunk, b: EvidenceChunk, settings: Optional[Any] = None) -> bool:
    """Number-claim sets must be disjoint AND the contents must differ beyond
    a shared opening prefix (``settings.conflict_prefix_chars`` chars)."""
    settings = settings or get_settings()
    prefix = settings.conflict_prefix_chars
    ca, cb = _claims(a), _claims(b)
    return bool(
        ca and cb and ca.isdisjoint(cb)
        and a.content[:prefix].lower() != b.content[:prefix].lower()
    )


def _in_force_at(chunk: EvidenceChunk, when: date) -> bool:
    start = chunk.effective_date or chunk.publication_date
    return start is None or start <= when


def resolve_pair(a: EvidenceChunk, b: EvidenceChunk, scenario_date: Optional[date]) -> tuple[EvidenceChunk, EvidenceChunk, str, bool]:
    if scenario_date:
        a_ok, b_ok = _in_force_at(a, scenario_date), _in_force_at(b, scenario_date)
        if a_ok != b_ok:
            kept, dropped = (a, b) if a_ok else (b, a)
            return kept, dropped, f"version en vigueur à la date du {scenario_date.isoformat()} retenue", True
    wa, wb = AUTHORITY_WEIGHTS[a.authority], AUTHORITY_WEIGHTS[b.authority]
    if wa != wb:
        kept, dropped = (a, b) if wa > wb else (b, a)
        return kept, dropped, f"source d'autorité supérieure retenue ({kept.authority.value} > {dropped.authority.value})", True
    da = a.publication_date or date.min
    db = b.publication_date or date.min
    if da != db:
        kept, dropped = (a, b) if da > db else (b, a)
        return kept, dropped, f"texte le plus récent retenu ({kept.publication_date} > {dropped.publication_date})", True
    kept, dropped = (a, b) if a.confidence >= b.confidence else (b, a)
    return kept, dropped, "conflit non résolu: les deux sources sont présentées", False


class ConflictResolverAgent(Agent):
    """Detects and resolves contradictions between retrieved sources."""

    name = "conflict_resolver"
    system_prompt = (
        "You are the conflict resolver. Compare sources that discuss the same legal article, "
        "detect contradictions, and keep the authoritative or most recent version."
    )
    tools = [
        t for t in list_tools()
        if t.name in ("detect_contradictions", "resolve_conflict_by_authority")
    ]

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        evidence = list(state.get("evidence", []))
        scenario = state.get("plan") and state["plan"].scenario_date
        if not scenario and state.get("scenario_date"):
            try:
                scenario = date.fromisoformat(state["scenario_date"])
            except ValueError:
                scenario = None

        groups: dict[tuple[str, str], list[EvidenceChunk]] = {}
        for chunk in evidence:
            key = _conflict_key(chunk)
            if key:
                groups.setdefault(key, []).append(chunk)

        conflicts: list[ConflictReport] = []
        dropped_ids: set[str] = set()
        settings = ctx.settings if ctx is not None else get_settings()
        for (doc, article), chunks in groups.items():
            for i in range(len(chunks)):
                for j in range(i + 1, len(chunks)):
                    a, b = chunks[i], chunks[j]
                    if a.chunk_id in dropped_ids or b.chunk_id in dropped_ids:
                        continue
                    if _contradict(a, b, settings):
                        kept, dropped, reason, resolved = resolve_pair(a, b, scenario)
                        dropped_ids.add(dropped.chunk_id)
                        conflicts.append(
                            ConflictReport(
                                topic=f"{doc} art. {article}",
                                kept_chunk_id=kept.chunk_id,
                                dropped_chunk_id=dropped.chunk_id,
                                reason=reason,
                                resolved=resolved,
                            )
                        )

        kept_evidence = [
            c for c in evidence
            if c.chunk_id not in {rep.dropped_chunk_id for rep in conflicts if rep.resolved}
        ]
        return {
            "evidence": kept_evidence,
            "conflicts": conflicts,
            "trace": [
                *state.get("trace", []),
                f"conflict_resolver: {len(conflicts)} conflicts ({sum(1 for c in conflicts if not c.resolved)} unresolved)",
            ],
        }


conflict_resolver_node = ConflictResolverAgent().run
