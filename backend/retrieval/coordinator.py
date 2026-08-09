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
from datetime import date
from typing import Optional

from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.embeddings import HashEmbeddings
from backend.core.models import EvidenceChunk, SearchTask
from backend.core.ports import RerankerProvider
from backend.retrieval.dedup import deduplicate
from backend.retrieval.fusion import reciprocal_rank_fusion
from backend.retrieval.reranker_providers import get_reranker
from backend.retrieval.temporal import TEMPORAL_INTENTS, passes_temporal_filter
from backend.retrieval.workers import worker_for

logger = logging.getLogger(__name__)


def _cache_key(
    tasks: list[SearchTask],
    namespace: str,
    temporal_intent: str = "any",
    scenario_date: Optional[date] = None,
) -> str:
    """Stable hash of the task set for result caching.

    The temporal intent is part of the key: a "current" and a "historical"
    run over the same tasks must never share filtered results.
    """
    payload = json.dumps(
        {
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "temporal_intent": temporal_intent,
            "scenario_date": scenario_date.isoformat() if scenario_date else None,
        },
        sort_keys=True,
        default=str,
    )
    return f"{namespace}{hashlib.sha256(payload.encode()).hexdigest()}"


class RetrievalCoordinator:
    """Parallel retrieval across all planned search tasks."""

    def __init__(self, ctx: AppContext, reranker: Optional[RerankerProvider] = None):
        self.ctx = ctx
        # Reranker is built once and injectable for tests. Default settings
        # resolve to the offline heuristic provider (spec §17), preserving the
        # previous behavior exactly (including the gated LLM rescore hook).
        settings = ctx.settings
        rerank_llm = ctx.llm if settings.rerank_llm_enabled else None
        self.reranker: RerankerProvider = reranker or get_reranker(
            settings, embedder=ctx.embedder, llm=rerank_llm
        )

    async def _run_task(self, task: SearchTask) -> list[EvidenceChunk]:
        worker = worker_for(task.kind, self.ctx)
        if worker is None:
            logger.warning("no worker for search kind %r", task.kind)
            return []
        return await worker.run(task)

    async def retrieve(
        self,
        tasks: list[SearchTask],
        *,
        temporal_intent: str = "any",
        scenario_date: Optional[date] = None,
    ) -> list[EvidenceChunk]:
        """Execute the retrieval plan: workers in parallel, then fuse/rerank.

        When ``temporal_intent`` is "current" or "historical" (spec §10, §24),
        fused candidates pass a hard temporal filter so current and historical
        versions are never silently mixed.  If the filter would empty the
        candidate set, the unfiltered results are kept and a warning is
        logged — retrieval never silently returns nothing.
        """
        if not tasks:
            return []

        settings = self.ctx.settings
        intent = (temporal_intent or "any").strip().lower()
        key = _cache_key(
            tasks,
            namespace=settings.retrieval_cache_namespace,
            temporal_intent=intent,
            scenario_date=scenario_date,
        )
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

        if intent in TEMPORAL_INTENTS and fused:
            applicable = [
                chunk
                for chunk in fused
                if passes_temporal_filter(chunk, intent, scenario_date)
            ]
            if applicable:
                if len(applicable) < len(fused):
                    logger.info(
                        "temporal filter (%s): kept %d/%d fused candidates",
                        intent, len(applicable), len(fused),
                    )
                fused = applicable
            else:
                logger.warning(
                    "temporal filter (%s) removed every candidate; "
                    "keeping unfiltered results instead of returning empty",
                    intent,
                )

        max_top_k = max((task.top_k for task in tasks), default=settings.default_top_k)
        # Score ALL candidates first, then apply the relevance floor, then
        # truncate — truncating before filtering would drop discriminative
        # matches ranked just below the cut. The LLM rescore hook (when
        # enabled) blends a 0-1 relevance judgment into the heuristic score.
        scored = await self.reranker.rerank(tasks[0].query, fused)
        # Relevance floor: a chunk must share real content tokens with the
        # query — otherwise irrelevant queries would still return
        # high-confidence but off-topic chunks. A single shared token is
        # enough when it is discriminative (rare across the candidate set,
        # e.g. "escroquerie"); generic shared words are not.
        min_shared_tokens = settings.retrieval_min_shared_tokens
        similarity_floor = settings.retrieval_similarity_floor
        weak_similarity_floor = settings.retrieval_weak_similarity_floor

        # Dense embedding models produce cosine scores that rarely exceed 0.5 for
        # good matches in a legal corpus. A floor of 0.7 rejects too much valid
        # evidence; cap it (default 0.45) when a real model is in use.
        # HashEmbeddings (offline/tests) can keep the configured floor because
        # its scores are already normalized/noisy.
        if not isinstance(self.ctx.embedder, HashEmbeddings):
            similarity_floor = min(
                similarity_floor, settings.retrieval_dense_similarity_floor_cap
            )

        term_df: dict[str, int] = {}
        for chunk in scored:
            for term in set(chunk.metadata.get("shared_terms", [])):
                term_df[term] = term_df.get(term, 0) + 1
        discriminative_cap = max(2, int(settings.retrieval_discriminative_df_ratio * len(scored)))

        def _relevant(chunk: EvidenceChunk) -> bool:
            shared = chunk.metadata.get("shared_tokens", 0)
            sim = chunk.metadata.get("query_similarity", 0.0)
            terms = chunk.metadata.get("shared_terms", [])
            has_discriminative = shared >= 1 and any(
                term_df[t] <= discriminative_cap for t in terms
            )
            if shared >= min_shared_tokens:
                return True
            if sim >= similarity_floor:
                return True  # strong semantic match (meaningful with real embeddings)
            # Weak lexical match: only keep if the shared word is rare across
            # the candidate set (e.g. "escroquerie"), otherwise generic words
            # like "droits" let unrelated Constitution articles through.
            if shared >= 1 and sim >= weak_similarity_floor and has_discriminative:
                return True
            return has_discriminative

        reranked = [chunk for chunk in scored if _relevant(chunk)][:max_top_k]

        # Graph expansion (spec §19): append articles related to the top
        # candidates via references/amends/repeals edges as low-score extras.
        # Additive and best-effort — never let the graph break retrieval.
        if reranked and getattr(settings, "legal_graph_enabled", True):
            try:
                from backend.retrieval.graph_worker import GraphWorker

                reranked = await GraphWorker(self.ctx).expand(reranked)
            except Exception:
                logger.warning("graph expansion failed; keeping fused results", exc_info=True)

        try:
            await self.ctx.cache.set(
                key,
                [chunk.model_dump(mode="json") for chunk in reranked],
                ttl=settings.retrieval_cache_ttl_seconds,
            )
        except Exception:
            pass
        return reranked
