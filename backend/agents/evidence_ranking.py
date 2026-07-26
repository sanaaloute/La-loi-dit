"""Evidence Ranking Agent: combines rerank score, retrieval score, source
confidence and authority weight into one ranking; weak evidence is ignored."""

from __future__ import annotations

from typing import Any

from backend.core.constants import AUTHORITY_WEIGHTS
from backend.core.context import AppContext
from backend.core.models import EvidenceChunk
from backend.core.state import GraphState


def final_score(chunk: EvidenceChunk, settings=None) -> float:
    from backend.core.config import get_settings

    cfg = settings or get_settings()
    relevance = max(chunk.rerank_score, chunk.retrieval_score)
    authority = AUTHORITY_WEIGHTS[chunk.authority]
    confidence = chunk.confidence if chunk.confidence > 0 else 0.5
    return (
        cfg.ranking_relevance_weight * relevance
        + cfg.ranking_authority_weight * authority
        + cfg.ranking_confidence_weight * confidence
    )


async def evidence_ranking_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    settings = ctx.settings
    evidence = list(state.get("evidence", []))
    scored = sorted(evidence, key=lambda c: final_score(c, settings), reverse=True)
    strong = [c for c in scored if final_score(c, settings) >= settings.min_evidence_score]
    return {
        "ranked_evidence": strong,
        "trace": [
            *state.get("trace", []),
            f"evidence_ranking: {len(strong)}/{len(evidence)} chunks kept after ranking",
        ],
    }
