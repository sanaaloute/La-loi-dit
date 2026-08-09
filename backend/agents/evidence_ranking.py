"""Evidence Ranking Agent.

Combines rerank score, retrieval score, source authority and source confidence
into one ranking; weak evidence is dropped.  This is a deterministic step in
charge of surfacing the most reliable evidence to the reasoning and response
agents.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.context import AppContext
from backend.core.models import EvidenceChunk
from backend.core.state import GraphState


class EvidenceRankingAgent(Agent):
    """Scores and ranks evidence after retrieval and conflict resolution."""

    name = "evidence_ranking"
    system_prompt = (
        "You are the evidence ranking agent. Rank retrieved chunks by relevance, "
        "authority and confidence, and drop anything that is too weak to support an answer."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        evidence = list(state.get("evidence", []))
        scored = sorted(evidence, key=lambda c: _final_score(c, ctx.settings), reverse=True)
        strong = [c for c in scored if _final_score(c, ctx.settings) >= ctx.settings.min_evidence_score]
        return {
            "ranked_evidence": strong,
            "trace": [
                *state.get("trace", []),
                f"evidence_ranking: {len(strong)}/{len(evidence)} chunks kept after ranking",
            ],
        }


def _final_score(chunk: EvidenceChunk, settings: Any | None = None) -> float:
    if settings is None:
        from backend.core.config import get_settings

        settings = get_settings()
    relevance = max(chunk.rerank_score, chunk.retrieval_score)
    authority = AUTHORITY_WEIGHTS[chunk.authority]
    confidence = chunk.confidence if chunk.confidence > 0 else 0.5
    return (
        settings.ranking_relevance_weight * relevance
        + settings.ranking_authority_weight * authority
        + settings.ranking_confidence_weight * confidence
    )


# Backwards-compatible alias used by tests.
final_score = _final_score

evidence_ranking_node = EvidenceRankingAgent().run
