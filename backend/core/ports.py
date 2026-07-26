"""Ports (protocols) decoupling the agent graph from infrastructure adapters.

Agents depend only on these interfaces; concrete adapters (Milvus, Redis,
PostgreSQL, in-memory) live in their own subsystems and are wired together
in `backend/core/context.py`.
"""

from __future__ import annotations

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
    async def delete(self, chunk_ids: list[str]) -> None: ...
    async def count(self) -> int: ...


class RetrieverProtocol(Protocol):
    """Executes the retrieval plan's search tasks, in parallel."""

    async def retrieve(self, tasks: list[SearchTask]) -> list[EvidenceChunk]: ...


class MemoryStoreProtocol(Protocol):
    async def load_buffer(self, session_id: str, limit: int = 20) -> list[ChatMessage]: ...
    async def append_turn(self, session_id: str, user_id: str, messages: list[ChatMessage]) -> None: ...
    async def recall(self, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...
    async def remember(self, record: MemoryRecord) -> None: ...
    async def get_preferences(self, user_id: str) -> dict[str, Any]: ...


class AuditLogProtocol(Protocol):
    async def log(self, event: str, actor: str, detail: dict[str, Any]) -> None: ...
