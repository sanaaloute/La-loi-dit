"""In-memory vector store implementing VectorStoreProtocol.

Default adapter for development, tests and offline operation: keeps
(chunk, vector) pairs in process memory and runs brute-force cosine
similarity search with metadata filtering. No external services needed.
"""

from __future__ import annotations

import asyncio
import math
from enum import Enum
from typing import Any, Optional

from backend.core.models import EvidenceChunk


def _norm(value: Any) -> str:
    """Normalize a filter value (enum or scalar) to a comparable string."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _values_match(chunk_value: Any, expected: Any) -> bool:
    """Match a chunk value against a filter value.

    Either side may be a scalar or a collection; a collection matches when
    the intersection is non-empty. Comparisons are enum-safe.
    """
    if chunk_value is None:
        return False
    expected_values = (
        list(expected) if isinstance(expected, (list, tuple, set)) else [expected]
    )
    chunk_values = (
        list(chunk_value)
        if isinstance(chunk_value, (list, tuple, set))
        else [chunk_value]
    )
    return any(
        _norm(c) == _norm(e) for c in chunk_values for e in expected_values
    )


def matches_filters(chunk: EvidenceChunk, filters: Optional[dict[str, Any]]) -> bool:
    """Return True when the chunk satisfies every filter entry.

    The special key ``legal_domains`` is matched against
    ``chunk.metadata["legal_domains"]`` (which may be a list); other keys are
    looked up in metadata first, then in chunk fields (e.g. ``source_kind``).
    A chunk that does not declare the filtered attribute at all is kept:
    domain filters narrow, they never exclude unclassified content.
    """
    if not filters:
        return True
    for key, expected in filters.items():
        if key == "legal_domains":
            chunk_value = chunk.metadata.get("legal_domains")
            if chunk_value is None:
                continue  # unclassified chunk: not excluded by the domain filter
        else:
            chunk_value = chunk.metadata.get(key, getattr(chunk, key, None))
        if not _values_match(chunk_value, expected):
            return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


class InMemoryVectorStore:
    """Brute-force in-memory vector store (VectorStoreProtocol)."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[EvidenceChunk, list[float]]] = {}
        self._lock = asyncio.Lock()

    async def upsert(
        self, chunks: list[EvidenceChunk], vectors: list[list[float]]
    ) -> None:
        """Insert or replace (chunk, vector) pairs keyed by chunk_id."""
        async with self._lock:
            for chunk, vector in zip(chunks, vectors):
                self._items[chunk.chunk_id] = (chunk, list(vector))

    async def add_texts(
        self, chunks: list[EvidenceChunk], vectors: list[list[float]]
    ) -> None:
        """Alias for upsert, for LangChain-style call sites."""
        await self.upsert(chunks, vectors)

    async def search(
        self,
        vector: list[float],
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[EvidenceChunk]:
        """Cosine-similarity search; scores are mapped to [0, 1]."""
        async with self._lock:
            items = list(self._items.values())
        scored: list[tuple[float, EvidenceChunk]] = []
        for chunk, stored in items:
            if not matches_filters(chunk, filters):
                continue
            score = _cosine(vector, stored)
            scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: list[EvidenceChunk] = []
        for score, chunk in scored[:top_k]:
            chunk.retrieval_score = max(0.0, min(1.0, (score + 1.0) / 2.0))
            results.append(chunk)
        return results

    async def delete(self, chunk_ids: list[str]) -> None:
        """Remove chunks by id; unknown ids are ignored."""
        async with self._lock:
            for chunk_id in chunk_ids:
                self._items.pop(chunk_id, None)

    async def count(self) -> int:
        """Number of stored chunks."""
        async with self._lock:
            return len(self._items)
