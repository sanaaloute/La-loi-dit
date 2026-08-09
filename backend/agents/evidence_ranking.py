"""Evidence Ranking Agent.

Combines rerank score, retrieval score, source authority and source confidence
into one ranking; weak evidence is dropped.  This is a deterministic step in
charge of surfacing the most reliable evidence to the reasoning and response
agents.  When the plan carries a temporal intent ("current"/"historical"), a
temporal score is blended in with weight ``ranking_temporal_weight`` so
time-inapplicable evidence sinks (spec §10, §24); intent "any" skips the
temporal component entirely, keeping legacy scores unchanged.
"""

from __future__ import annotations

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
        return {
            "ranked_evidence": strong,
            "trace": [
                *state.get("trace", []),
                f"evidence_ranking: {len(strong)}/{len(evidence)} chunks kept after ranking",
            ],
        }


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
