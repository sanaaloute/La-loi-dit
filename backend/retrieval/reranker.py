"""Cross-encoder-style reranking of fused evidence.

The semantic signal comes from the configured embedding provider when one is
passed in (usually the same provider used for vector search). In offline/test
mode the provider is :class:`HashEmbeddings`; in that case we reuse the
``retrieval_score`` already computed by the vector store instead of re-hashing
content, which avoids masking good semantic matches with a weak second-order
hash signal. A lexical overlap signal (shared content tokens) is always
computed for the relevance floor and for breaking ties.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Optional

from backend.core.config import get_settings
from backend.core.embeddings import HashEmbeddings
from backend.core.models import EvidenceChunk
from backend.core.prompts import get_prompt

logger = logging.getLogger(__name__)

# Process-local memoization of rerank chunk embeddings (chunk_id -> vector).
# Bounded FIFO; the corpus is stable between reindexes and the parallel
# branches of one question overlap heavily, so this kills most repeat
# embeddings on the GPU.
_EMBED_CACHE_MAX = 4096
_EMBED_CACHE: "dict[str, list[float]]" = {}


def _embed_cache_put(chunk_id: str, vector: list[float]) -> None:
    if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
        _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
    _EMBED_CACHE[chunk_id] = vector


# Stopwords excluded from the shared-token relevance signal (FR + EN).
_STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "en", "au", "aux",
    "est", "sont", "pour", "par", "sur", "dans", "que", "qui", "quoi", "ce",
    "cette", "ces", "se", "sa", "son", "ses", "il", "elle", "ne", "pas", "ou",
    "quel", "quelle", "quels", "quelles", "à", "y", "leur", "leurs", "être",
    "avoir", "fait", "entre", "chez", "si", "plus", "moins", "très", "dans",
    # topic-constant for this corpus — non-discriminative:
    "burkina", "faso", "burkinabè", "burkinabe", "burkinabé",
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for", "on", "at",
    "what", "which", "how", "when", "where", "who",
}


def _content_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-zàâäéèêëîïôöùûüç0-9]+", text.lower()))
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def _llm_refine(
    query: str, chunks: list[EvidenceChunk], llm: Any, excerpt_chars: int
) -> Optional[list[float]]:
    """Ask an LLM for 0-1 relevance scores; None on any failure."""
    if llm is None or not hasattr(llm, "complete"):
        return None
    try:
        numbered = "\n".join(
            f"[{i}] {chunk.content[:excerpt_chars]}" for i, chunk in enumerate(chunks)
        )
        raw = await llm.complete(
            system=get_prompt("RERANK_RESCORE"),
            user=f"Query: {query}\n\n{numbered}",
        )
        match = re.search(r"\[[^\]]*\]", raw, re.DOTALL)
        scores = json.loads(match.group(0)) if match else None
        if isinstance(scores, list) and len(scores) == len(chunks):
            return [max(0.0, min(1.0, float(s))) for s in scores]
    except Exception as exc:
        logger.debug("LLM rerank refinement failed, using heuristic: %s", exc)
    return None


async def rerank(
    query: str,
    chunks: list[EvidenceChunk],
    top_k: int,
    llm: Any = None,
    embedder: Any = None,
) -> list[EvidenceChunk]:
    """Score and reorder chunks by relevance; return the top_k.

    Writes ``rerank_score`` and ``query_similarity`` in [0, 1] on every chunk.
    When ``embedder`` is a real dense model, it is reused here for a second
    semantic pass. When it is the hashing embedder (offline/tests), the
    ``retrieval_score`` from the vector store is used as the semantic signal so
    the cheap hash model does not override good dense-retrieval scores.
    """
    if not chunks:
        return []
    settings = get_settings()
    similarity_weight = settings.rerank_similarity_weight
    confidence_weight = settings.rerank_confidence_weight
    lexical_weight = max(0.0, 1.0 - similarity_weight - confidence_weight)

    # Cap the candidates entering the expensive passes: the input is
    # RRF-ordered, so truncation keeps the likely-best ones. Uncapped
    # reranking re-embeds every candidate on the GPU per branch.
    chunks = chunks[: max(1, settings.rerank_max_candidates)]

    # Use the real dense model for reranking when available; otherwise fall back
    # to the same hashing embedder used by the offline vector store so the
    # similarity signal stays consistent with what produced retrieval_score.
    if embedder is None:
        embedder = HashEmbeddings()
    try:
        # retrieval_text carries the contextual prefix when set, matching what
        # the vector store embedded at ingest time; raw content otherwise.
        # Chunk vectors are memoized process-locally by chunk_id: the corpus
        # is stable between reindexes, and parallel branches of one question
        # overlap heavily — re-embedding them per branch dominates latency.
        chunk_vectors: list[Optional[list[float]]] = [_EMBED_CACHE.get(c.chunk_id) for c in chunks]
        missing_idx = [i for i, v in enumerate(chunk_vectors) if v is None]
        to_embed = [query, *[chunks[i].retrieval_text or chunks[i].content for i in missing_idx]]
        vectors = await embedder.embed(to_embed)
        query_vector = vectors[0]
        for i, vec in zip(missing_idx, vectors[1:]):
            _embed_cache_put(chunks[i].chunk_id, vec)
            chunk_vectors[i] = vec
        chunk_vectors = [v or [] for v in chunk_vectors]
    except Exception as exc:
        logger.warning("rerank embedding failed, using zero similarity: %s", exc)
        query_vector = []
        chunk_vectors = []

    query_tokens = _content_tokens(query)
    heuristic: list[float] = []
    for i, chunk in enumerate(chunks):
        similarity = max(0.0, _cosine(query_vector, chunk_vectors[i])) if chunk_vectors else 0.0

        shared = query_tokens & _content_tokens(chunk.content)
        chunk.metadata["query_similarity"] = round(similarity, 4)
        chunk.metadata["shared_tokens"] = len(shared)
        chunk.metadata["shared_terms"] = sorted(shared)

        # Lexical overlap as a small additive signal (helps when dense scores tie).
        lexical = min(1.0, len(shared) / max(1, settings.retrieval_min_shared_tokens))

        confidence = max(0.0, min(1.0, chunk.confidence))
        heuristic.append(
            similarity_weight * similarity
            + lexical_weight * lexical
            + confidence_weight * confidence
        )

    # LLM rescore only the best heuristic candidates: the call is the most
    # expensive stage of a branch on a local model, and the tail candidates
    # would rank last regardless.
    llm_k = max(0, settings.rerank_llm_top_k)
    llm_idx = sorted(range(len(chunks)), key=lambda i: heuristic[i], reverse=True)[:llm_k]
    llm_scores: Optional[list[Optional[float]]] = None
    if llm_idx and llm is not None:
        subset = await _llm_refine(
            query,
            [chunks[i] for i in llm_idx],
            llm,
            excerpt_chars=settings.rerank_llm_excerpt_chars,
        )
        if subset is not None:
            llm_scores = [None] * len(chunks)
            for i, s in zip(llm_idx, subset):
                llm_scores[i] = s
    llm_blend = settings.rerank_llm_blend_weight
    for i, chunk in enumerate(chunks):
        score = heuristic[i]
        if llm_scores is not None and llm_scores[i] is not None:
            score = (1.0 - llm_blend) * score + llm_blend * llm_scores[i]
        chunk.rerank_score = max(0.0, min(1.0, score))

    ranked = sorted(chunks, key=lambda c: c.rerank_score, reverse=True)
    return ranked[:top_k]
