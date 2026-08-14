"""Ports (protocols) decoupling the agent graph from infrastructure adapters.

Agents depend only on these interfaces; concrete adapters (Milvus, Redis,
PostgreSQL, in-memory) live in their own subsystems and are wired together
in `backend/core/context.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Protocol

from backend.core.models import (
    ChatMessage,
    EvidenceChunk,
    MemoryRecord,
    SearchTask,
)


class VectorStoreProtocol(Protocol):
    async def upsert(self, chunks: list[EvidenceChunk], vectors: list[list[float]]) -> None: ...
    async def search(
        self,
        vector: list[float],
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[EvidenceChunk]: ...
    async def get_by_ids(self, chunk_ids: list[str]) -> list[EvidenceChunk]: ...
    async def count_by_document_id(self, document_id: str) -> int: ...
    async def delete(self, chunk_ids: list[str]) -> None: ...
    async def count(self) -> int: ...


class RetrieverProtocol(Protocol):
    """Executes the retrieval plan's search tasks, in parallel."""

    async def retrieve(
        self,
        tasks: list[SearchTask],
        *,
        temporal_intent: str = "any",
        scenario_date: Optional[date] = None,
    ) -> list[EvidenceChunk]: ...


class RerankerProvider(Protocol):
    """Reorders fused evidence by relevance (spec §17, §47).

    Implementations MUST set ``rerank_score`` in [0, 1] on every chunk and
    return the chunks sorted by that score, descending. They never raise:
    a broken backend falls back to the offline heuristic reranker.
    """

    async def rerank(self, query: str, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]: ...


class MemoryStoreProtocol(Protocol):
    async def load_buffer(self, session_id: str, limit: int = 20) -> list[ChatMessage]: ...
    async def append_turn(self, session_id: str, user_id: str, messages: list[ChatMessage]) -> None: ...
    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]: ...
    async def get_session_messages(self, user_id: str, session_id: str) -> list[ChatMessage]: ...
    async def recall(self, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...
    async def remember(self, record: MemoryRecord) -> None: ...
    async def get_preferences(self, user_id: str) -> dict[str, Any]: ...


class AuditLogProtocol(Protocol):
    async def log(self, event: str, actor: str, detail: dict[str, Any]) -> None: ...
