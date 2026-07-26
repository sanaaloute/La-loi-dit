"""Milvus vector store adapter (production).

pymilvus is imported lazily inside ``connect`` so the module imports cleanly
in environments where the dependency or the server is absent. Every public
method raises a clean ``RetrievalError`` on connection/operation failure —
callers (the factory, the coordinator) catch it and fall back gracefully.

Each EvidenceChunk is serialized to JSON in a VARCHAR field; the primary key
is ``chunk_id`` and the vector field uses an HNSW index with COSINE metric.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from pydantic import ValidationError

from backend.core.config import Settings
from backend.core.exceptions import RetrievalError
from backend.core.models import EvidenceChunk
from backend.vectorstore.memory_store import matches_filters

class MilvusVectorStore:
    """Milvus-backed vector store implementing VectorStoreProtocol."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._collection = settings.milvus_collection
        self._dim = settings.embedding_dimension
        self._client: Any = None

    async def connect(self) -> None:
        """Establish the connection and create the collection if missing.

        Raises:
            RetrievalError: pymilvus missing, server unreachable, or schema
                creation failed.
        """
        try:
            from pymilvus import DataType, MilvusClient
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RetrievalError(f"pymilvus not installed: {exc}") from exc

        uri = f"http://{self._settings.milvus_host}:{self._settings.milvus_port}"
        try:
            self._client = await asyncio.to_thread(MilvusClient, uri=uri)
            exists = await asyncio.to_thread(
                self._client.has_collection, self._collection
            )
            if not exists:
                schema = MilvusClient.create_schema(
                    auto_id=False, enable_dynamic_field=False
                )
                schema.add_field(
                    "chunk_id", DataType.VARCHAR, is_primary=True, max_length=64
                )
                schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self._dim)
                schema.add_field("chunk_json", DataType.VARCHAR, max_length=65535)
                index_params = self._client.prepare_index_params()
                index_params.add_index(
                    field_name="vector",
                    index_type="HNSW",
                    metric_type="COSINE",
                    params={"M": 16, "efConstruction": 200},
                )
                await asyncio.to_thread(
                    self._client.create_collection,
                    collection_name=self._collection,
                    schema=schema,
                    index_params=index_params,
                )
            # Fail fast here (not on first search) when the server is down.
            await asyncio.to_thread(self._client.list_collections)
        except RetrievalError:
            raise
        except Exception as exc:
            self._client = None
            raise RetrievalError(f"Milvus connection failed ({uri}): {exc}") from exc

    def _require_client(self) -> Any:
        if self._client is None:
            raise RetrievalError("MilvusVectorStore is not connected")
        return self._client

    async def upsert(
        self, chunks: list[EvidenceChunk], vectors: list[list[float]]
    ) -> None:
        """Insert or replace chunks with their embedding vectors."""
        client = self._require_client()
        rows = [
            {
                "chunk_id": chunk.chunk_id,
                "vector": list(vector),
                "chunk_json": chunk.model_dump_json(),
            }
            for chunk, vector in zip(chunks, vectors)
        ]
        if not rows:
            return
        try:
            await asyncio.to_thread(client.upsert, self._collection, rows)
        except Exception as exc:
            raise RetrievalError(f"Milvus upsert failed: {exc}") from exc

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
        """ANN search (HNSW/COSINE); metadata filters applied client-side."""
        client = self._require_client()
        overfetch = getattr(self._settings, "milvus_filter_overfetch", 4)
        limit = top_k * overfetch if filters else top_k
        try:
            hits = await asyncio.to_thread(
                client.search,
                collection_name=self._collection,
                data=[list(vector)],
                limit=limit,
                output_fields=["chunk_json"],
                search_params={"metric_type": "COSINE"},
            )
        except Exception as exc:
            raise RetrievalError(f"Milvus search failed: {exc}") from exc

        results: list[EvidenceChunk] = []
        for hit in hits[0] if hits else []:
            try:
                chunk = EvidenceChunk.model_validate_json(
                    hit["entity"]["chunk_json"]
                )
            except (KeyError, ValidationError, ValueError):
                continue
            if not matches_filters(chunk, filters):
                continue
            # Milvus COSINE returns a similarity in [-1, 1]; map to [0, 1].
            distance = float(hit.get("distance", 0.0))
            chunk.retrieval_score = max(0.0, min(1.0, (distance + 1.0) / 2.0))
            results.append(chunk)
            if len(results) >= top_k:
                break
        return results

    async def delete(self, chunk_ids: list[str]) -> None:
        """Delete chunks by primary key."""
        if not chunk_ids:
            return
        client = self._require_client()
        try:
            await asyncio.to_thread(
                client.delete, collection_name=self._collection, ids=list(chunk_ids)
            )
        except Exception as exc:
            raise RetrievalError(f"Milvus delete failed: {exc}") from exc

    async def count(self) -> int:
        """Number of entities in the collection."""
        client = self._require_client()
        try:
            stats = await asyncio.to_thread(
                client.get_collection_stats, self._collection
            )
            return int(stats.get("row_count", 0))
        except Exception as exc:
            raise RetrievalError(f"Milvus count failed: {exc}") from exc
