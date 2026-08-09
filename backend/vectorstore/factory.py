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
        # Retry a few times: the first handshake(s) can flap on NAT'd or
        # filtered links (WSL<->Windows, VPNs) while the server is fine.
        attempts = max(1, settings.milvus_connect_attempts)
        for attempt in range(1, attempts + 1):
            try:
                from backend.vectorstore.milvus_store import MilvusVectorStore

                store = MilvusVectorStore(settings)
                await asyncio.wait_for(store.connect(), timeout=settings.milvus_connect_timeout_seconds)
                logger.info("vector store: Milvus (%s)", settings.milvus_collection)
                return store
            except Exception as exc:
                if attempt == attempts:
                    logger.warning(
                        "Milvus unavailable (%s); using in-memory store",
                        str(exc) or type(exc).__name__,
                    )
                else:
                    logger.debug(
                        "Milvus connect attempt %d/%d failed (%s); retrying",
                        attempt,
                        attempts,
                        str(exc) or type(exc).__name__,
                    )
                    await asyncio.sleep(settings.milvus_connect_backoff_seconds * attempt)
    return InMemoryVectorStore()
