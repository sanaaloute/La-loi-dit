"""Answer cache for chat: exact-match + conservative near-duplicate lookup.

Layered on ``ctx.cache`` (Redis or in-memory — the same abstraction used
everywhere). Two levels:

1. **Exact** — key ``ans:x:<sha256(normalize(query)|cache_identity)>`` where
   normalize = lowercase, strip punctuation, collapse whitespace. Value is
   the ChatResponse JSON, TTL ``settings.cache_ttl_seconds``.
2. **Semantic** — a tiny JSON index (single key, capped at
   ``settings.answer_cache_max_index`` most recent entries) holding each
   entry's query embedding. On exact-miss, cosine similarity against the
   index; a hit requires >= ``settings.answer_cache_semantic_threshold``
   (0.98 — legal answers must not cross-contaminate).

Cacheability rules (enforced by callers / ``is_cacheable``):
- only non-refused answers with confidence >= settings.confidence_threshold
  and requires_human_review == False;
- requests carrying an explicit ``session_id`` are treated as
  mid-conversation and BYPASS the cache entirely (the same query can mean
  something different with prior context);
- the cache identity covers user scope, language, scenario date, model and
  embedding roles, corpus generation, prompts, and generation settings.

Every failure degrades to a cache miss — never an error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Optional

from backend.core.config import Settings
from backend.core.llm import CLASSIFICATION_MODEL, ROLE_MODEL_CONFIGS
from backend.core.prompts import PROMPTS
from backend.retrieval.bm25 import corpus_generation

logger = logging.getLogger(__name__)

_EXACT_PREFIX = "ans:x:"
_INDEX_KEY = "ans:index"

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = _PUNCT_RE.sub(" ", query.lower())
    return _SPACE_RE.sub(" ", text).strip()


def _prompt_generation(settings: Settings) -> str:
    built_in = hashlib.sha256(
        json.dumps(PROMPTS, sort_keys=True).encode()
    ).hexdigest()
    overrides = ""
    if settings.prompts_dir:
        root = Path(settings.prompts_dir)
        files = [*root.glob("*.md"), *root.glob("*.txt")] if root.is_dir() else []
        if files:
            overrides = str(max(path.stat().st_mtime_ns for path in files))
    return f"{built_in}:{overrides}"


def cache_identity(
    settings: Settings,
    *,
    model_id: str,
    language: Optional[str] = None,
    scenario_date: Any = None,
    user_id: Optional[str] = None,
) -> str:
    """Fingerprint every request input that can change a cached legal answer."""
    payload = {
        "model_id": model_id,
        "classification_model": CLASSIFICATION_MODEL,
        "role_model_configs": ROLE_MODEL_CONFIGS,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "language": language or "",
        "scenario_date": str(scenario_date) if scenario_date else "",
        "user_id": user_id or "anonymous",
        "corpus_generation": corpus_generation(settings.data_dir),
        "prompt_generation": _prompt_generation(settings),
        "app_version": settings.app_version,
        "llm_provider": settings.llm_provider,
        "llm_temperature": settings.llm_temperature,
        "fast_lane_enabled": settings.fast_lane_enabled,
        "fast_lane_min_coverage": settings.fast_lane_min_coverage,
        "confidence_citation_weight": settings.confidence_citation_weight,
        "confidence_coverage_weight": settings.confidence_coverage_weight,
        "confidence_legal_support_weight": settings.confidence_legal_support_weight,
        "reranker_provider": settings.reranker_provider,
        "reranker_model": settings.reranker_model,
        "rerank_llm_enabled": settings.rerank_llm_enabled,
    }
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def exact_key(query: str, model_id: str) -> str:
    digest = hashlib.sha256(f"{normalize_query(query)}|{model_id}".encode()).hexdigest()
    return _EXACT_PREFIX + digest


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def is_cacheable(response_json: dict[str, Any], settings: Settings) -> bool:
    """Eligibility: solid, non-refused answers only."""
    answer = response_json.get("answer") or {}
    if answer.get("refused"):
        return False
    if answer.get("requires_human_review"):
        return False
    return answer.get("confidence", 0.0) >= settings.confidence_threshold


class AnswerCache:
    """Exact + near-duplicate cache over the shared cache backend."""

    def __init__(self, cache: Any, embedder: Any, settings: Settings):
        self.cache = cache
        self.embedder = embedder
        self.settings = settings

    # ------------------------------------------------------------------
    async def get(self, query: str, model_id: str) -> Optional[dict[str, Any]]:
        """Return the cached ChatResponse JSON, or None (miss/disabled)."""
        if not self.settings.answer_cache_enabled:
            return None
        key = exact_key(query, model_id)
        try:
            hit = await self.cache.get(key)
            if isinstance(hit, dict):
                return hit
        except Exception:
            pass
        return await self._semantic_get(query, model_id)

    async def set(self, query: str, model_id: str, response_json: dict[str, Any]) -> None:
        """Store a response; failures are silently ignored."""
        if not self.settings.answer_cache_enabled:
            return
        key = exact_key(query, model_id)
        try:
            await self.cache.set(key, response_json, ttl=self.settings.cache_ttl_seconds)
        except Exception:
            return
        await self._index_add(query, key, model_id)

    # ------------------------------------------------------------------
    # Level 2: conservative semantic lookup
    # ------------------------------------------------------------------

    async def _embed(self, text: str) -> Optional[list[float]]:
        try:
            return (await self.embedder.embed([text]))[0]
        except Exception:
            return None

    async def _load_index(self) -> list[dict[str, Any]]:
        try:
            raw = await self.cache.get(_INDEX_KEY)
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    async def _semantic_get(self, query: str, model_id: str) -> Optional[dict[str, Any]]:
        try:
            index = await self._load_index()
            if not index:
                return None
            vector = await self._embed(normalize_query(query))
            if vector is None:
                return None
            threshold = self.settings.answer_cache_semantic_threshold
            for entry in reversed(index):  # most recent first
                # Near-identical queries may still differ by user, legal date,
                # language, corpus, or model configuration.
                if entry.get("model") != model_id:
                    continue
                if _cosine(vector, entry.get("embedding") or []) >= threshold:
                    hit = await self.cache.get(entry["key"])
                    return hit if isinstance(hit, dict) else None
        except Exception:
            pass  # level 2 is best-effort
        return None

    async def _index_add(self, query: str, key: str, model_id: str) -> None:
        try:
            vector = await self._embed(normalize_query(query))
            if vector is None:
                return
            index = await self._load_index()
            index = [e for e in index if e.get("key") != key]
            index.append({"key": key, "model": model_id, "embedding": vector, "created_at": time.time()})
            index = index[-self.settings.answer_cache_max_index :]
            await self.cache.set(_INDEX_KEY, index, ttl=self.settings.cache_ttl_seconds)
        except Exception:
            pass
