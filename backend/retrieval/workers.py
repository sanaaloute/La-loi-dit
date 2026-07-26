"""Retrieval workers: one async worker per SearchKind.

Each worker executes a single SearchTask against its backend (vector store,
BM25 corpus, or the official-source orchestrator) and returns evidence
chunks. Workers never talk to each other; the coordinator runs them all in
parallel and fuses their lists.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from backend.core.context import AppContext
from backend.core.models import EvidenceChunk, SearchKind, SearchTask

logger = logging.getLogger(__name__)


class BaseWorker:
    """Common interface for retrieval workers."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    def _fetch_k(self) -> int:
        return getattr(self.ctx.settings, "retrieval_fetch_k", 20)

    async def run(self, task: SearchTask) -> list[EvidenceChunk]:
        """Execute one search task; implementations must not raise."""
        raise NotImplementedError

    async def _embed_query(self, query: str) -> list[float]:
        """Embed a query, with per-text caching in ctx.cache."""
        key = f"retrieval:emb:{hashlib.sha256(query.encode()).hexdigest()}"
        try:
            cached = await self.ctx.cache.get(key)
            if cached is not None:
                return list(cached)
        except Exception:
            pass
        vector = (await self.ctx.embedder.embed([query]))[0]
        try:
            await self.ctx.cache.set(key, vector)
        except Exception:
            pass
        return vector


class VectorWorker(BaseWorker):
    """Semantic search over the configured vector store."""

    async def run(self, task: SearchTask) -> list[EvidenceChunk]:
        if self.ctx.vector_store is None:
            return []
        vector = await self._embed_query(task.query)
        top_k = max(task.top_k, self._fetch_k())
        return await self.ctx.vector_store.search(
            vector, top_k=top_k, filters=task.filters or None
        )


class KeywordWorker(BaseWorker):
    """BM25 keyword search over the corpus held in ctx.extras["bm25"]."""

    async def run(self, task: SearchTask) -> list[EvidenceChunk]:
        from backend.retrieval.bm25 import BM25Retriever

        retriever: Optional[Any] = self.ctx.extras.get("bm25")
        if retriever is None:
            retriever = BM25Retriever()
            self.ctx.extras["bm25"] = retriever
        top_k = max(task.top_k, self._fetch_k())
        return retriever.search(task.query, top_k=top_k, filters=task.filters or None)


class _OrchestratorWorker(BaseWorker):
    """Base for workers delegating to the official-source orchestrator."""

    kinds: tuple[SearchKind, ...] = ()

    async def run(self, task: SearchTask) -> list[EvidenceChunk]:
        from backend.search.orchestrator import search_sources
        from backend.search.sources import DEFAULT_REGISTRY, sources_for_kind

        registry = [s for kind in self.kinds for s in sources_for_kind(kind, DEFAULT_REGISTRY)]
        # dedupe sources seen across overlapping kinds
        registry = list({source.name: source for source in registry}.values())
        return await search_sources([task], registry=registry)


class GovernmentWorker(_OrchestratorWorker):
    """Government portals and ministry sites."""

    kinds = (SearchKind.GOVERNMENT,)


class NewsWorker(_OrchestratorWorker):
    """Official news and press sources."""

    kinds = (SearchKind.NEWS, SearchKind.GOVERNMENT)


class RegulationWorker(_OrchestratorWorker):
    """Laws, decrees, gazette and OHADA texts."""

    kinds = (SearchKind.REGULATION,)


class CaseLawWorker(_OrchestratorWorker):
    """Court decisions and constitutional council rulings."""

    kinds = (SearchKind.CASE_LAW,)


class WebWorker(_OrchestratorWorker):
    """Open web search across every registered source."""

    kinds = (SearchKind.WEB,)


class UploadedWorker(BaseWorker):
    """Semantic search restricted to user-uploaded documents."""

    async def run(self, task: SearchTask) -> list[EvidenceChunk]:
        if self.ctx.vector_store is None:
            return []
        vector = await self._embed_query(task.query)
        filters = dict(task.filters or {})
        filters["source_kind"] = SearchKind.UPLOADED.value
        top_k = max(task.top_k, self._fetch_k())
        return await self.ctx.vector_store.search(vector, top_k=top_k, filters=filters)


WORKER_REGISTRY: dict[SearchKind, type[BaseWorker]] = {
    SearchKind.VECTOR: VectorWorker,
    SearchKind.KEYWORD: KeywordWorker,
    SearchKind.GOVERNMENT: GovernmentWorker,
    SearchKind.NEWS: NewsWorker,
    SearchKind.REGULATION: RegulationWorker,
    SearchKind.CASE_LAW: CaseLawWorker,
    SearchKind.WEB: WebWorker,
    SearchKind.WEBSITE: WebWorker,
    SearchKind.UPLOADED: UploadedWorker,
}


def worker_for(kind: SearchKind, ctx: AppContext) -> Optional[BaseWorker]:
    """Instantiate the worker for a search kind; None for unknown kinds."""
    worker_cls = WORKER_REGISTRY.get(kind)
    return worker_cls(ctx) if worker_cls is not None else None
