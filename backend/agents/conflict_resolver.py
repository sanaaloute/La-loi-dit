"""Conflict Resolver.

When two sources disagree, resolution follows the spec's order:
1. prefer official government source (authority weight),
2. prefer the latest law / amendment (publication & effective dates),
3. legal timeline reasoning: for a scenario date, prefer the version in
   force at that date.
Unresolvable conflicts are kept and surfaced — never silently guessed away.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.context import AppContext
from backend.core.models import ConflictReport, EvidenceChunk
from backend.core.state import GraphState


def _conflict_key(chunk: EvidenceChunk) -> Optional[tuple[str, str]]:
    """Chunks about the same article of the same code are comparable."""
    if chunk.document_name and chunk.article:
        return (chunk.document_name.lower().strip(), str(chunk.article).strip())
    return None


_FR_NUMBER_WORDS = {
    "un", "une", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
    "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    "vingt", "trente", "quarante", "cinquante", "soixante", "cent", "mille",
}


def _claims(chunk: EvidenceChunk) -> set[str]:
    """Extract crude factual claims (digits and French number words) for
    contradiction detection — legal texts often write numbers out in words."""
    text = chunk.content.lower()
    claims = set(re.findall(r"\d+(?:[.,]\d+)?", text))
    claims |= {
        w for w in re.findall(r"[a-zàâäéèêëîïôöùûüç]+", text) if w in _FR_NUMBER_WORDS
    }
    return claims


def _contradict(a: EvidenceChunk, b: EvidenceChunk) -> bool:
    ca, cb = _claims(a), _claims(b)
    return bool(ca and cb and ca.isdisjoint(cb) and a.content[:80].lower() != b.content[:80].lower())


def _in_force_at(chunk: EvidenceChunk, when: date) -> bool:
    start = chunk.effective_date or chunk.publication_date
    return start is None or start <= when


def resolve_pair(a: EvidenceChunk, b: EvidenceChunk, scenario_date: Optional[date]) -> tuple[EvidenceChunk, EvidenceChunk, str, bool]:
    """Return (kept, dropped, reason, resolved)."""
    # 1. legal timeline: if only one version is in force at the scenario date, it wins
    if scenario_date:
        a_ok, b_ok = _in_force_at(a, scenario_date), _in_force_at(b, scenario_date)
        if a_ok != b_ok:
            kept, dropped = (a, b) if a_ok else (b, a)
            return kept, dropped, f"version en vigueur à la date du {scenario_date.isoformat()} retenue", True
    # 2. authority level (Constitution > amended law > decree > ... > news > blog)
    wa, wb = AUTHORITY_WEIGHTS[a.authority], AUTHORITY_WEIGHTS[b.authority]
    if wa != wb:
        kept, dropped = (a, b) if wa > wb else (b, a)
        return kept, dropped, f"source d'autorité supérieure retenue ({kept.authority.value} > {dropped.authority.value})", True
    # 3. recency (amendments and latest laws win)
    da = a.publication_date or date.min
    db = b.publication_date or date.min
    if da != db:
        kept, dropped = (a, b) if da > db else (b, a)
        return kept, dropped, f"texte le plus récent retenu ({kept.publication_date} > {dropped.publication_date})", True
    # unresolvable: keep higher-confidence, flag uncertainty
    kept, dropped = (a, b) if a.confidence >= b.confidence else (b, a)
    return kept, dropped, "conflit non résolu: les deux sources sont présentées", False


async def conflict_resolver_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
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
    for (doc, article), chunks in groups.items():
        for i in range(len(chunks)):
            for j in range(i + 1, len(chunks)):
                a, b = chunks[i], chunks[j]
                if a.chunk_id in dropped_ids or b.chunk_id in dropped_ids:
                    continue
                if _contradict(a, b):
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
        "trace": [*state.get("trace", []), f"conflict_resolver: {len(conflicts)} conflicts ({sum(1 for c in conflicts if not c.resolved)} unresolved)"],
    }
