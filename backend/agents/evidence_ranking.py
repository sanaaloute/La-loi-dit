"""Evidence Ranking Agent.

Combines rerank score, retrieval score, source authority and source confidence
into one ranking; weak evidence is dropped, and chunks pointing to the same
provision (same document, same article, same lifecycle status) are merged so
each citation number resolves to a distinct source.  This is a deterministic
step in charge of surfacing the most reliable evidence to the reasoning and
response agents.  When the plan carries a temporal intent ("current"/"historical"), a
temporal score is blended in with weight ``ranking_temporal_weight`` so
time-inapplicable evidence sinks (spec §10, §24); intent "any" skips the
temporal component entirely, keeping legacy scores unchanged.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from backend.agents.agent import Agent
from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.context import AppContext
from backend.core.models import EvidenceChunk
from backend.core.state import GraphState
from backend.retrieval.temporal import TEMPORAL_INTENTS, temporal_score


class EvidenceRankingAgent(Agent):
    """Scores and ranks evidence after retrieval and conflict resolution."""

    name = "evidence_ranking"
    system_prompt = (
        "You are the evidence ranking agent. Rank retrieved chunks by relevance, "
        "authority and confidence, and drop anything that is too weak to support an answer."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        evidence = list(state.get("evidence", []))
        plan = state.get("plan")
        temporal_intent = plan.temporal_intent if plan else "any"
        scenario_date = plan.scenario_date if plan else None
        if scenario_date is None and state.get("scenario_date"):
            try:
                scenario_date = date.fromisoformat(state["scenario_date"])
            except ValueError:
                scenario_date = None

        def score(chunk: EvidenceChunk) -> float:
            return _final_score(
                chunk, ctx.settings,
                temporal_intent=temporal_intent, scenario_date=scenario_date,
            )

        scored = sorted(evidence, key=score, reverse=True)
        strong = [c for c in scored if score(c) >= ctx.settings.min_evidence_score]
        deduped = _dedupe_same_source(strong)
        selected = _select_per_sub_question(
            deduped,
            state.get("branch_membership", []),
            plan,
            max_per_group=ctx.settings.answer_max_evidence_per_subquestion,
        )
        return {
            "ranked_evidence": selected,
            "trace": [
                *state.get("trace", []),
                f"evidence_ranking: {len(selected)}/{len(evidence)} chunks kept after ranking"
                f" (max {ctx.settings.answer_max_evidence_per_subquestion} per sub-question)"
                + (f" ({len(strong) - len(deduped)} duplicates merged)" if len(deduped) < len(strong) else ""),
            ],
        }


def _select_per_sub_question(
    chunks: list[EvidenceChunk],
    membership: list[dict[str, str]],
    plan: Any,
    *,
    max_per_group: int,
) -> list[EvidenceChunk]:
    """Keep the best ``max_per_group`` chunks of each sub-question.

    ``chunks`` arrives score-sorted and deduped. Every retrieval branch
    records which sub-question produced each chunk (``branch_membership``);
    each sub-question keeps its own best chunks above the score threshold,
    so one dominant sub-question cannot starve the others of evidence.
    Expanded parents inherit the group of the child that triggered them.
    Chunks without membership information form one trailing group, capped
    the same way.
    """
    owner: dict[str, str] = {}
    for entry in membership:
        owner.setdefault(entry.get("chunk_id", ""), entry.get("query", ""))

    group_order: list[str] = []
    if plan is not None:
        group_order.extend(q for q in plan.sub_questions if q.strip())
    for entry in membership:
        q = entry.get("query", "")
        if q and q not in group_order:
            group_order.append(q)

    groups: dict[str, list[EvidenceChunk]] = {q: [] for q in group_order}
    other: list[EvidenceChunk] = []
    for chunk in chunks:
        q = owner.get(chunk.chunk_id)
        if q is None:
            # Expanded parent: inherit the sub-question of the child chunk
            # whose retrieval triggered the expansion.
            for child in chunk.child_chunks or []:
                if child.chunk_id in owner:
                    q = owner[child.chunk_id]
                    break
        if q is not None and q in groups:
            groups[q].append(chunk)
        else:
            other.append(chunk)

    selected: list[EvidenceChunk] = []
    seen: set[str] = set()

    def take(group: list[EvidenceChunk]) -> None:
        kept = 0
        for chunk in group:
            if kept >= max_per_group:
                break
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            selected.append(chunk)
            kept += 1

    for q in group_order:
        take(groups[q])
    take(other)
    return selected


def _dedupe_same_source(chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    """Collapse chunks that point to the same legal provision.

    The index holds several chunks per article (overlapping windows, expanded
    parents next to standalone hits), which produced answers citing e.g. [9]
    and [10] for the very same article.  ``chunks`` arrives sorted best-score
    first, so the first occurrence of each provision wins.  The lifecycle
    status stays in the key so an in-force version never swallows a repealed
    one (temporal reasoning needs both).
    """
    seen: set[tuple[str, str, str]] = set()
    deduped: list[EvidenceChunk] = []
    for chunk in chunks:
        doc = chunk.document_id or chunk.document_name
        article = re.sub(r"\s+", " ", (chunk.article or "").strip().lower())
        if article:
            key = (doc, article, chunk.status)
        else:
            # No article metadata: merge only exact duplicate passages, never
            # distinct sections of the same document.
            content = re.sub(r"\s+", " ", chunk.content.strip().lower())
            key = (doc, "", content)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def _final_score(
    chunk: EvidenceChunk,
    settings: Any | None = None,
    temporal_intent: str = "any",
    scenario_date: Optional[date] = None,
) -> float:
    if settings is None:
        from backend.core.config import get_settings

        settings = get_settings()
    relevance = max(chunk.rerank_score, chunk.retrieval_score)
    authority = AUTHORITY_WEIGHTS[chunk.authority]
    confidence = chunk.confidence if chunk.confidence > 0 else 0.5
    base = (
        settings.ranking_relevance_weight * relevance
        + settings.ranking_authority_weight * authority
        + settings.ranking_confidence_weight * confidence
    )
    intent = (temporal_intent or "any").strip().lower()
    if intent not in TEMPORAL_INTENTS:
        return base  # no temporal discrimination: legacy score unchanged
    # Blend the temporal score with its own weight and renormalize, so the
    # result stays a weighted average over active components.
    temporal_weight = getattr(settings, "ranking_temporal_weight", 0.15)
    base_weight = (
        settings.ranking_relevance_weight
        + settings.ranking_authority_weight
        + settings.ranking_confidence_weight
    )
    temporal = temporal_score(chunk, intent, scenario_date)
    return (base + temporal_weight * temporal) / (base_weight + temporal_weight)


# Backwards-compatible alias used by tests.
final_score = _final_score

evidence_ranking_node = EvidenceRankingAgent().run
