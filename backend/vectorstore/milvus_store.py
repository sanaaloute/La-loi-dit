"""Milvus vector store adapter (production).

pymilvus is imported lazily inside ``connect`` so the module imports cleanly
in environments where the dependency or the server is absent. Every public
method raises a clean ``RetrievalError`` on connection/operation failure —
callers (the factory, the coordinator) catch it and fall back gracefully.

Each EvidenceChunk is serialized to JSON in a VARCHAR field; the primary key
is ``chunk_id``.  ``document_id``, ``article``, ``status`` and
``document_type`` are scalar fields: they are filtered natively via Milvus
``expr`` (everything else falls back to client-side post-filtering with
``matches_filters``), and ``document_id`` powers per-document list/delete.
The vector field uses an HNSW index with COSINE metric.  On connect, a
collection whose schema predates any required scalar field is dropped and
recreated (log a warning): such a collection breaks document-level
operations and native filtering, so re-ingestion is required anyway.
"""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Any, Optional

from pydantic import ValidationError

from backend.core.config import Settings
from backend.core.exceptions import RetrievalError
from backend.core.models import EvidenceChunk
from backend.vectorstore.memory_store import matches_filters

logger = logging.getLogger(__name__)

# Scalar fields every collection must carry (besides chunk_id/vector/chunk_json).
_REQUIRED_FIELDS = {
    "chunk_id",
    "vector",
    "chunk_json",
    "document_id",
    "article",
    "status",
    "document_type",
}

# Filter keys promoted to native Milvus ``expr`` filtering; all other filter
# keys keep the client-side ``matches_filters`` fallback.
_NATIVE_FILTER_FIELDS = ("document_id", "article", "status", "document_type")


def _norm_filter_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _scalar(chunk: EvidenceChunk, field: str) -> str:
    """Value written to a native scalar column ("" when unset)."""
    value = getattr(chunk, field, None)
    if value is None:
        return ""
    return _norm_filter_value(value)


def build_native_filter_expr(
    filters: Optional[dict[str, Any]],
) -> tuple[Optional[str], set[str]]:
    """Split filters into a native Milvus expr and client-side remainder.

    Returns ``(expr, native_keys)``: ``expr`` covers the promoted scalar
    fields (``document_id``/``article``/``status``/``document_type``) or is
    None, and ``native_keys`` lists the filter keys it covers so the caller
    can post-filter only the rest.  Values containing a double quote are left
    to the client-side path (keeps expr escaping trivially safe).
    """
    if not filters:
        return None, set()
    parts: list[str] = []
    native_keys: set[str] = set()
    for key in _NATIVE_FILTER_FIELDS:
        if key not in filters:
            continue
        expected = filters[key]
        values = (
            list(expected)
            if isinstance(expected, (list, tuple, set))
            else [expected]
        )
        normalized = [_norm_filter_value(v) for v in values]
        if any('"' in v for v in normalized):
            continue  # unsafe to inline: client-side fallback handles it
        quoted = ", ".join(f'"{v}"' for v in normalized)
        parts.append(f"{key} in [{quoted}]")
        native_keys.add(key)
    return (" and ".join(parts) if parts else None), native_keys


def _import_pymilvus():
    """Import pymilvus without its ``load_dotenv()`` side effect.

    pymilvus loads the project ``.env`` into ``os.environ`` at import time;
    those values then shadow the ``.env.dev`` overrides pydantic applies when
    building Settings (e.g. MILVUS_HOST=localhost), breaking local runs.
    """
    before = dict(os.environ)
    try:
        from pymilvus import DataType, MilvusClient
    finally:
        for key in set(os.environ) - set(before):
            os.environ.pop(key, None)
        for key, value in before.items():
            if os.environ.get(key) != value:
                os.environ[key] = value
    return DataType, MilvusClient

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
            DataType, MilvusClient = _import_pymilvus()
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RetrievalError(f"pymilvus not installed: {exc}") from exc

        uri = self._settings.milvus_uri or f"http://{self._settings.milvus_host}:{self._settings.milvus_port}"
        try:
            self._client = await asyncio.to_thread(MilvusClient, uri=uri)
            exists = await asyncio.to_thread(
                self._client.has_collection, self._collection
            )
            if exists:
                # Self-heal an outdated schema: a collection missing a required
                # scalar field breaks document-level operations and native
                # filtering, so it must be recreated (re-ingestion is required
                # afterwards).
                desc = await asyncio.to_thread(
                    self._client.describe_collection, self._collection
                )
                fields = {f.get("name") for f in desc.get("fields", [])}
                if not _REQUIRED_FIELDS <= fields:
                    logger.warning(
                        "Milvus collection %r has an outdated schema (%s); "
                        "dropping and recreating it — re-ingest documents afterwards",
                        self._collection,
                        sorted(f for f in fields if f),
                    )
                    await asyncio.to_thread(self._client.drop_collection, self._collection)
                    exists = False
            if not exists:
                schema = MilvusClient.create_schema(
                    auto_id=False, enable_dynamic_field=False
                )
                schema.add_field(
                    "chunk_id", DataType.VARCHAR, is_primary=True, max_length=64
                )
                schema.add_field("document_id", DataType.VARCHAR, max_length=64)
                # Promoted scalars for native expr filtering (spec §11).
                schema.add_field("article", DataType.VARCHAR, max_length=128)
                schema.add_field("status", DataType.VARCHAR, max_length=32)
                schema.add_field("document_type", DataType.VARCHAR, max_length=32)
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
            else:
                # On reconnect (Milvus Lite file or server restart) an existing
                # collection is released; search/query fail until it is loaded.
                await asyncio.to_thread(self._client.load_collection, self._collection)
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
                "document_id": chunk.document_id,
                "article": _scalar(chunk, "article"),
                "status": _scalar(chunk, "status"),
                "document_type": _scalar(chunk, "document_type"),
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
        """ANN search (HNSW/COSINE).

        Promoted scalar fields (``document_id``/``article``/``status``/
        ``document_type``) are filtered natively via Milvus ``expr``; every
        other filter key keeps the client-side post-filter with
        ``matches_filters`` (overfetch xN to compensate).
        """
        client = self._require_client()
        expr, native_keys = build_native_filter_expr(filters)
        remaining = (
            {k: v for k, v in filters.items() if k not in native_keys}
            if filters
            else None
        )
        overfetch = getattr(self._settings, "milvus_filter_overfetch", 4)
        limit = top_k * overfetch if remaining else top_k
        kwargs: dict[str, Any] = {
            "collection_name": self._collection,
            "data": [list(vector)],
            "limit": limit,
            "output_fields": ["chunk_json"],
            "search_params": {"metric_type": "COSINE"},
        }
        if expr:
            kwargs["filter"] = expr
        try:
            hits = await asyncio.to_thread(client.search, **kwargs)
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
            if not matches_filters(chunk, remaining):
                continue
            # Milvus COSINE returns a similarity in [-1, 1]; map to [0, 1].
            distance = float(hit.get("distance", 0.0))
            chunk.retrieval_score = max(0.0, min(1.0, (distance + 1.0) / 2.0))
            results.append(chunk)
            if len(results) >= top_k:
                break
        return results

    async def get_by_ids(self, chunk_ids: list[str]) -> list[EvidenceChunk]:
        """Fetch chunks by chunk_id from Milvus."""
        if not chunk_ids:
            return []
        client = self._require_client()
        # Build a filter expression for the varchar primary key.
        quoted = [f"'{cid}'" for cid in chunk_ids]
        filter_expr = f"chunk_id in [{','.join(quoted)}]"
        try:
            hits = await asyncio.to_thread(
                client.query,
                collection_name=self._collection,
                filter=filter_expr,
                output_fields=["chunk_json"],
            )
        except Exception as exc:
            raise RetrievalError(f"Milvus get_by_ids failed: {exc}") from exc

        results: list[EvidenceChunk] = []
        for hit in hits or []:
            try:
                chunk = EvidenceChunk.model_validate_json(hit["chunk_json"])
            except (KeyError, ValidationError, ValueError):
                continue
            results.append(chunk)
        return results

    async def get_by_document_id(self, document_id: str) -> list[EvidenceChunk]:
        """Fetch all chunks belonging to a logical document."""
        client = self._require_client()
        try:
            hits = await asyncio.to_thread(
                client.query,
                collection_name=self._collection,
                filter=f"document_id == '{document_id}'",
                output_fields=["chunk_json"],
            )
        except Exception as exc:
            raise RetrievalError(f"Milvus get_by_document_id failed: {exc}") from exc

        results: list[EvidenceChunk] = []
        for hit in hits or []:
            try:
                chunk = EvidenceChunk.model_validate_json(hit["chunk_json"])
            except (KeyError, ValidationError, ValueError):
                continue
            results.append(chunk)
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

    async def delete_by_document_id(self, document_id: str) -> int:
        """Delete all chunks belonging to a logical document. Returns rows deleted."""
        client = self._require_client()
        try:
            hits = await asyncio.to_thread(
                client.query,
                collection_name=self._collection,
                filter=f"document_id == '{document_id}'",
                output_fields=["chunk_id"],
            )
        except Exception as exc:
            raise RetrievalError(f"Milvus query for document deletion failed: {exc}") from exc
        ids = [h["chunk_id"] for h in (hits or []) if "chunk_id" in h]
        if ids:
            await self.delete(ids)
        return len(ids)
