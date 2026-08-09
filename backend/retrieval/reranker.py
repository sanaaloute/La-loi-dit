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

logger = logging.getLogger(__name__)

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
    query: str, chunks: list[EvidenceChunk], llm: Any
) -> Optional[list[float]]:
    """Ask an LLM for 0-1 relevance scores; None on any failure."""
    if llm is None or not hasattr(llm, "complete"):
        return None
    try:
        numbered = "\n".join(
            f"[{i}] {chunk.content[:300]}" for i, chunk in enumerate(chunks)
        )
        raw = await llm.complete(
            system=(
                "Score each passage's relevance to the query from 0.0 to 1.0. "
                'Reply with a JSON array of floats only, e.g. [0.9, 0.2].'
            ),
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

    # Use the real dense model for reranking when available; otherwise fall back
    # to the same hashing embedder used by the offline vector store so the
    # similarity signal stays consistent with what produced retrieval_score.
    if embedder is None:
        embedder = HashEmbeddings()
    try:
        vectors = await embedder.embed([query, *[chunk.content for chunk in chunks]])
        query_vector = vectors[0]
        chunk_vectors = vectors[1:]
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

    llm_scores = await _llm_refine(query, chunks, llm)
    for i, chunk in enumerate(chunks):
        score = heuristic[i]
        if llm_scores is not None:
            score = 0.5 * score + 0.5 * llm_scores[i]
        chunk.rerank_score = max(0.0, min(1.0, score))

    ranked = sorted(chunks, key=lambda c: c.rerank_score, reverse=True)
    return ranked[:top_k]
