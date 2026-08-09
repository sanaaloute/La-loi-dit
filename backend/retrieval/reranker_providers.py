"""Configurable reranker providers (spec §17, §47).

The default :class:`HeuristicReranker` wraps the existing offline rerank
scoring (embedding cosine + lexical overlap + confidence, optional LLM
rescore) and needs zero credentials. :class:`ApiCrossEncoderReranker` calls
an OpenAI-compatible/Cohere-style ``/rerank`` endpoint (BGE, Qwen, Cohere
rerank, other cross-encoders) and falls back to the heuristic reranker on
any failure — retrieval never fails because a rerank endpoint is down.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from backend.core.config import Settings
from backend.core.embeddings import EmbeddingProvider
from backend.core.models import EvidenceChunk
from backend.core.ports import RerankerProvider
from backend.retrieval.reranker import rerank as heuristic_rerank

logger = logging.getLogger(__name__)


class HeuristicReranker:
    """Offline cross-encoder-style reranker (the existing default scoring).

    Delegates to :func:`backend.retrieval.reranker.rerank`; scoring behavior
    (weights, metadata, optional LLM rescore blend) is unchanged.
    """

    def __init__(self, embedder: Optional[EmbeddingProvider] = None, llm: Any = None):
        self.embedder = embedder
        self.llm = llm

    async def rerank(self, query: str, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
        return await heuristic_rerank(
            query, chunks, top_k=len(chunks), llm=self.llm, embedder=self.embedder
        )


class ApiCrossEncoderReranker:
    """HTTP cross-encoder client for Cohere-style / OpenAI-compatible rerank APIs.

    POSTs ``{model, query, documents}`` to the rerank endpoint and expects
    ``{"results": [{"index": int, "relevance_score": float}]}`` (Cohere,
    Jina, and OpenAI-compatible rerank servers such as vLLM/TEI all use this
    shape). Batches documents, retries up to ``settings.reranker_max_retries``
    times, and on any failure falls back to the heuristic reranker with a
    warning — never raises.
    """

    def __init__(
        self,
        settings: Settings,
        fallback: Optional[RerankerProvider] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.settings = settings
        self.fallback: RerankerProvider = fallback or HeuristicReranker()
        self._client = client or httpx.AsyncClient(
            timeout=settings.reranker_timeout_seconds
        )

    async def rerank(self, query: str, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
        if not chunks:
            return []
        try:
            scores = await self._score_with_retry(query, chunks)
        except Exception as exc:
            logger.warning(
                "api reranker failed; falling back to heuristic reranker: %s",
                exc,
                extra={
                    "reranker_provider": "api",
                    "reranker_model": self.settings.reranker_model,
                    "chunks": len(chunks),
                },
            )
            return await self.fallback.rerank(query, chunks)
        for chunk, score in zip(chunks, scores):
            chunk.rerank_score = max(0.0, min(1.0, float(score)))
        return sorted(chunks, key=lambda c: c.rerank_score, reverse=True)

    async def _score_with_retry(self, query: str, chunks: list[EvidenceChunk]) -> list[float]:
        """Retry up to ``reranker_max_retries`` times; the caller falls back
        to the heuristic reranker if every attempt fails."""
        attempts = 1 + max(0, self.settings.reranker_max_retries)
        for attempt in range(1, attempts + 1):
            try:
                return await self._score(query, chunks)
            except Exception as exc:
                if attempt >= attempts:
                    raise
                logger.info(
                    "api reranker attempt %d/%d failed, retrying: %s",
                    attempt, attempts, exc,
                )
        raise AssertionError("unreachable")  # pragma: no cover

    async def _score(self, query: str, chunks: list[EvidenceChunk]) -> list[float]:
        batch_size = max(1, self.settings.reranker_batch_size)
        scores = [0.0] * len(chunks)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            scores[start : start + len(batch)] = await self._score_batch(query, batch)
        return scores

    async def _score_batch(self, query: str, batch: list[EvidenceChunk]) -> list[float]:
        base = (self.settings.reranker_api_base or "").rstrip("/")
        url = base if base.endswith("/rerank") else f"{base}/rerank"
        response = await self._client.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.reranker_api_key}"},
            json={
                "model": self.settings.reranker_model,
                "query": query,
                "documents": [chunk.content for chunk in batch],
            },
        )
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list) or len(results) != len(batch):
            raise ValueError(
                f"rerank endpoint returned {0 if not isinstance(results, list) else len(results)} "
                f"scores for {len(batch)} documents"
            )
        ordered = sorted(results, key=lambda item: item["index"])
        return [float(item["relevance_score"]) for item in ordered]


def get_reranker(
    settings: Settings,
    embedder: Optional[EmbeddingProvider] = None,
    llm: Any = None,
) -> RerankerProvider:
    """Pick the reranker from settings; offline heuristic is the default.

    ``reranker_provider="api"`` without full credentials (api base, key, and
    model) degrades to the heuristic reranker with a warning instead of
    failing — an offline install must always work.
    """
    heuristic: RerankerProvider = HeuristicReranker(embedder=embedder, llm=llm)
    provider = (settings.reranker_provider or "heuristic").strip().lower()
    if provider == "api":
        if settings.reranker_api_base and settings.reranker_api_key and settings.reranker_model:
            return ApiCrossEncoderReranker(settings, fallback=heuristic)
        logger.warning(
            "reranker_provider='api' requires reranker_api_base, reranker_api_key "
            "and reranker_model; using the offline heuristic reranker"
        )
    elif provider != "heuristic":
        logger.warning(
            "unknown reranker_provider %r; using the offline heuristic reranker",
            settings.reranker_provider,
        )
    return heuristic
