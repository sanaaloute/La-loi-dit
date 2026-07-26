"""Deduplication of retrieved evidence chunks.

Drops exact content-hash duplicates and near-duplicates (normalized-text
equality or token-Jaccard similarity above 0.9), always keeping the
higher-scored copy.
"""

from __future__ import annotations

import hashlib
import re

from backend.core.config import get_settings
from backend.core.models import EvidenceChunk

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _normalize(text: str) -> str:
    """Whitespace/case-normalized text for equality and hashing."""
    return " ".join(text.lower().split())


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _score(chunk: EvidenceChunk) -> float:
    return max(chunk.retrieval_score, chunk.rerank_score, chunk.confidence)


def _prefer(current: EvidenceChunk, candidate: EvidenceChunk) -> EvidenceChunk:
    """Keep the higher-scored copy; ties keep the earlier one."""
    return candidate if _score(candidate) > _score(current) else current


def deduplicate(
    chunks: list[EvidenceChunk],
    threshold: float | None = None,
) -> list[EvidenceChunk]:
    """Remove exact and near-duplicate chunks, preserving score order."""
    if threshold is None:
        threshold = get_settings().dedup_jaccard_threshold
    kept: list[EvidenceChunk] = []
    seen_hashes: dict[str, int] = {}  # content hash -> index in kept
    for chunk in sorted(chunks, key=_score, reverse=True):
        normalized = _normalize(chunk.content)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        if digest in seen_hashes:
            idx = seen_hashes[digest]
            kept[idx] = _prefer(kept[idx], chunk)
            continue
        chunk_tokens = _tokens(normalized)
        duplicate_of = None
        for idx, existing in enumerate(kept):
            if _jaccard(chunk_tokens, _tokens(_normalize(existing.content))) > threshold:
                duplicate_of = idx
                break
        if duplicate_of is not None:
            kept[duplicate_of] = _prefer(kept[duplicate_of], chunk)
        else:
            seen_hashes[digest] = len(kept)
            kept.append(chunk)
    return kept
