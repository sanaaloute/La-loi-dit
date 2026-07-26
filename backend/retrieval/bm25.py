"""BM25 keyword retriever over an in-memory EvidenceChunk corpus.

Wraps ``rank_bm25.BM25Okapi`` when the package is installed (lazy import at
first use); otherwise a compact TF-IDF fallback in this file keeps keyword
search fully functional offline. Tokenization is French-friendly: lowercase,
accent-stripped, split on non-alphanumerics.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Optional

from backend.core.models import EvidenceChunk
from backend.vectorstore.memory_store import matches_filters

_SPLIT_RE = re.compile(r"[^0-9a-z]+")


def tokenize(text: str) -> list[str]:
    """French-sane tokenizer: lowercase, strip accents, split non-alnum."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if not unicodedata.combining(c))
    return [token for token in _SPLIT_RE.split(ascii_text) if token]


class _TfidfFallback:
    """Minimal TF-IDF scorer used when rank_bm25 is unavailable."""

    def __init__(self, corpus: list[list[str]]):
        self._corpus = corpus
        n_docs = max(1, len(corpus))
        df: dict[str, int] = {}
        for doc in corpus:
            for token in set(doc):
                df[token] = df.get(token, 0) + 1
        self._idf = {
            token: math.log((n_docs - count + 0.5) / (count + 0.5) + 1.0)
            for token, count in df.items()
        }

    def get_scores(self, query: list[str]) -> list[float]:
        scores = []
        for doc in self._corpus:
            tf: dict[str, int] = {}
            for token in doc:
                tf[token] = tf.get(token, 0) + 1
            score = sum(
                (tf.get(token, 0) / max(1, len(doc))) * self._idf.get(token, 0.0)
                for token in query
            )
            scores.append(score)
        return scores


class BM25Retriever:
    """Keyword retriever over EvidenceChunk content (BM25 or TF-IDF)."""

    def __init__(self) -> None:
        self._chunks: list[EvidenceChunk] = []
        self._scorer: Any = None
        self._backend = "tfidf"

    def _rebuild(self) -> None:
        corpus = [tokenize(chunk.content) for chunk in self._chunks]
        try:
            from rank_bm25 import BM25Okapi

            self._scorer = BM25Okapi(corpus)
            self._backend = "bm25"
        except ImportError:
            self._scorer = _TfidfFallback(corpus)
            self._backend = "tfidf"

    def add_documents(self, chunks: list[EvidenceChunk]) -> None:
        """Add chunks to the corpus and rebuild the index."""
        self._chunks.extend(chunks)
        self._rebuild()

    @property
    def size(self) -> int:
        return len(self._chunks)

    def search(
        self,
        query: str,
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[EvidenceChunk]:
        """Score the corpus; return top_k chunks with scores in [0, 1]."""
        if not self._chunks or self._scorer is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        raw_scores = self._scorer.get_scores(query_tokens)
        scored = [
            (float(score), chunk)
            for score, chunk in zip(raw_scores, self._chunks)
            if matches_filters(chunk, filters)
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[:top_k]
        max_score = top[0][0] if top and top[0][0] > 0 else 1.0
        results: list[EvidenceChunk] = []
        for score, chunk in top:
            chunk.retrieval_score = max(0.0, min(1.0, score / max_score))
            results.append(chunk)
        return results
