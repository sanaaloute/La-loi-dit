"""Shared score conversions for retrieval and ranking."""

from __future__ import annotations

from backend.core.models import EvidenceChunk

DEFAULT_SOURCE_RELIABILITY = 0.5


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def cosine_to_unit(value: float) -> float:
    """Use the same absolute cosine scale in vector search and reranking."""
    return clamp_unit(value)


def bm25_to_unit(value: float) -> float:
    """Map a non-normalized BM25 score monotonically to [0, 1)."""
    positive = max(0.0, float(value))
    return positive / (positive + 1.0)


def source_reliability(chunk: EvidenceChunk) -> float:
    """Read the independent reliability field, defaulting only when it is unset."""
    if chunk.confidence <= 0:
        return DEFAULT_SOURCE_RELIABILITY
    return clamp_unit(chunk.confidence)
