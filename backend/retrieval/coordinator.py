"""Retrieval Coordinator: runs all workers in parallel, then fuses and reranks.

Implements RetrieverProtocol. Worker failures are isolated (logged into
``ctx.extras["retrieval_errors"]``) so one broken backend never sinks the
whole retrieval step. Results are cached per task-set under the
``retrieval:`` namespace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.models import EvidenceChunk, SearchTask
from backend.retrieval.dedup import deduplicate
from backend.retrieval.fusion import reciprocal_rank_fusion
from backend.retrieval.reranker import rerank
from backend.retrieval.workers import worker_for

logger = logging.getLogger(__name__)


def _cache_key(tasks: list[SearchTask], namespace: str) -> str:
    """Stable hash of the task set for result caching."""
    payload = json.dumps(
        [task.model_dump(mode="json") for task in tasks],
        sort_keys=True,
        default=str,
    )
    return f"{namespace}{hashlib.sha256(payload.encode()).hexdigest()}"


class RetrievalCoordinator:
    """Parallel retrieval across all planned search tasks."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    async def _run_task(self, task: SearchTask) -> list[EvidenceChunk]:
        worker = worker_for(task.kind, self.ctx)
        if worker is None:
            logger.warning("no worker for search kind %r", task.kind)
            return []
        return await worker.run(task)

    async def retrieve(self, tasks: list[SearchTask]) -> list[EvidenceChunk]:
        """Execute the retrieval plan: workers in parallel, then fuse/rerank."""
        if not tasks:
            return []

        settings = self.ctx.settings
        key = _cache_key(tasks, namespace=settings.retrieval_cache_namespace)
        try:
            cached = await self.ctx.cache.get(key)
        except Exception:
            cached = None
        if cached:
            try:
                return [EvidenceChunk.model_validate(item) for item in cached]
            except Exception:
                pass  # corrupt cache entry: recompute

        results = await asyncio.gather(
            *(self._run_task(task) for task in tasks),
            return_exceptions=True,
        )
        error_log = self.ctx.extras.setdefault("retrieval_errors", [])
        result_lists: list[list[EvidenceChunk]] = []
        for task, result in zip(tasks, results):
            if isinstance(result, Exception):
                message = f"{task.kind.value} worker failed: {result}"
                logger.warning(message)
                error_log.append(message)
            elif result:
                result_lists.append(result)

        merged = deduplicate([chunk for lst in result_lists for chunk in lst])
        fused = reciprocal_rank_fusion(result_lists) if result_lists else []
        # Merge deduplicated survivors back in RRF order (fusion already
        # dedupes by chunk_id; the explicit dedup pass drops near-duplicates).
        kept_ids = {chunk.chunk_id for chunk in merged}
        fused = [chunk for chunk in fused if chunk.chunk_id in kept_ids]

        max_top_k = max((task.top_k for task in tasks), default=settings.default_top_k)
        # Score ALL candidates first, then apply the relevance floor, then
        # truncate — truncating before filtering would drop discriminative
        # matches ranked just below the cut.
        scored = await rerank(tasks[0].query, fused, top_k=len(fused), llm=None)
        # Relevance floor: a chunk must share real content tokens with the
        # query — otherwise irrelevant queries would still return
        # high-confidence but off-topic chunks. A single shared token is
        # enough when it is discriminative (rare across the candidate set,
        # e.g. "escroquerie"); generic shared words are not.
        min_shared_tokens = settings.retrieval_min_shared_tokens
        similarity_floor = settings.retrieval_similarity_floor
        weak_similarity_floor = settings.retrieval_weak_similarity_floor
        term_df: dict[str, int] = {}
        for chunk in scored:
            for term in set(chunk.metadata.get("shared_terms", [])):
                term_df[term] = term_df.get(term, 0) + 1
        discriminative_cap = max(2, int(0.3 * len(scored)))

        def _relevant(chunk: EvidenceChunk) -> bool:
            shared = chunk.metadata.get("shared_tokens", 0)
            sim = chunk.metadata.get("query_similarity", 0.0)
            if shared >= min_shared_tokens:
                return True
            if sim >= similarity_floor:
                return True  # strong semantic match (meaningful with real embeddings)
            if shared >= 1 and sim >= weak_similarity_floor:
                return True
            return shared >= 1 and any(
                term_df[t] <= discriminative_cap
                for t in chunk.metadata.get("shared_terms", [])
            )

        reranked = [chunk for chunk in scored if _relevant(chunk)][:max_top_k]

        try:
            await self.ctx.cache.set(
                key, [chunk.model_dump(mode="json") for chunk in reranked]
            )
        except Exception:
            pass
        return reranked
