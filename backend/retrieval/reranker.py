"""Cross-encoder-style reranking of fused evidence.

Default path is a fully offline heuristic: cosine similarity between
hashed-token vectors (``HashEmbeddings``) of the query and each chunk,
blended with the chunk's source confidence. When an LLM client is passed,
it may refine the heuristic scores; any failure silently keeps them.
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
) -> list[EvidenceChunk]:
    """Score and reorder chunks by relevance; return the top_k.

    Writes ``rerank_score`` in [0, 1] on every chunk. The offline heuristic
    (hashed-token cosine blended with confidence) is always computed and is
    the default; an LLM, when provided, only refines it.
    """
    if not chunks:
        return []
    settings = get_settings()
    similarity_weight = settings.rerank_similarity_weight
    confidence_weight = settings.rerank_confidence_weight
    embedder = HashEmbeddings()
    texts = [query, *[chunk.content for chunk in chunks]]
    vectors = await embedder.embed(texts)
    query_vector = vectors[0]

    heuristic: list[float] = []
    query_tokens = _content_tokens(query)
    for chunk, vector in zip(chunks, vectors[1:]):
        similarity = max(0.0, _cosine(query_vector, vector))
        shared = query_tokens & _content_tokens(chunk.content)
        chunk.metadata["query_similarity"] = round(similarity, 4)
        chunk.metadata["shared_tokens"] = len(shared)
        chunk.metadata["shared_terms"] = sorted(shared)
        confidence = max(0.0, min(1.0, chunk.confidence))
        heuristic.append(
            similarity_weight * similarity + confidence_weight * confidence
        )

    llm_scores = await _llm_refine(query, chunks, llm)
    for i, chunk in enumerate(chunks):
        score = heuristic[i]
        if llm_scores is not None:
            score = 0.5 * score + 0.5 * llm_scores[i]
        chunk.rerank_score = max(0.0, min(1.0, score))

    ranked = sorted(chunks, key=lambda c: c.rerank_score, reverse=True)
    return ranked[:top_k]
