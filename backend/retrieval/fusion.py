"""Reciprocal Rank Fusion across parallel retriever result lists."""

from __future__ import annotations

from typing import Optional

from backend.core.config import get_settings
from backend.core.models import EvidenceChunk


def reciprocal_rank_fusion(
    result_lists: list[list[EvidenceChunk]], k: Optional[int] = None
) -> list[EvidenceChunk]:
    if k is None:
        k = get_settings().rrf_k
    """Merge ranked lists with standard RRF: score += 1 / (k + rank).

    Duplicates (same chunk_id) accumulate scores across lists, keeping the
    highest-scoring copy's payload. Final RRF scores are written into
    ``retrieval_score``, normalized so the top result scores 1.0.
    """
    best: dict[str, tuple[float, EvidenceChunk]] = {}
    for results in result_lists:
        for rank, chunk in enumerate(results, start=1):
            contribution = 1.0 / (k + rank)
            entry = best.get(chunk.chunk_id)
            if entry is None:
                best[chunk.chunk_id] = (contribution, chunk)
            else:
                score, kept = entry
                if chunk.retrieval_score > kept.retrieval_score:
                    kept = chunk
                best[chunk.chunk_id] = (score + contribution, kept)

    fused = sorted(best.values(), key=lambda pair: pair[0], reverse=True)
    top_score = fused[0][0] if fused and fused[0][0] > 0 else 1.0
    merged: list[EvidenceChunk] = []
    for score, chunk in fused:
        chunk.retrieval_score = score / top_score
        merged.append(chunk)
    return merged
