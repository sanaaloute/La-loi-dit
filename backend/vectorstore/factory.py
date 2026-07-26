"""Vector store factory: Milvus when enabled and reachable, in-memory otherwise.

Never raises — the application must always boot with a working vector store,
even with every external service down.
"""

from __future__ import annotations

import asyncio
import logging

from backend.core.config import Settings
from backend.core.ports import VectorStoreProtocol
from backend.vectorstore.memory_store import InMemoryVectorStore

logger = logging.getLogger(__name__)


async def get_vector_store(settings: Settings) -> VectorStoreProtocol:
    """Return the best available vector store; fall back to in-memory.

    Attempts a quick Milvus connection only when ``settings.milvus_enabled``
    is set. Any failure (missing pymilvus, unreachable server, timeout)
    silently downgrades to the in-memory store.
    """
    if settings.milvus_enabled:
        try:
            from backend.vectorstore.milvus_store import MilvusVectorStore

            store = MilvusVectorStore(settings)
            await asyncio.wait_for(store.connect(), timeout=settings.milvus_connect_timeout_seconds)
            logger.info("vector store: Milvus (%s)", settings.milvus_collection)
            return store
        except Exception as exc:
            logger.warning("Milvus unavailable (%s); using in-memory store", exc)
    return InMemoryVectorStore()
